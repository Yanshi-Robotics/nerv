"""One turn, end to end: the platform side of NERV/Brain.

`Turn` implements the brain's Link. It assembles observations, routes tool calls (body,
tool node, or the brain's own registers), puts every body action to the safety gate, runs
actions on a thread so progress keeps flowing and the operator can stop them, records
everything in the session and the log, and emits the operator-facing events.
"""
from __future__ import annotations

import base64
import contextvars
import dataclasses
import logging
import queue
import threading
import time
import uuid
from typing import Callable

from .. import config
from ..harness.context import ContextOverflow
from ..brain import prompts
from ..nerve.body import ActionResult, ToolSpec
from ..nerve.brain import CallTool, Say, SetRegister, Think, ToolResult, UserMessage
from ..nerve import operator as op
from . import interrupt, messages, trust
from .client import RemoteBody, RemoteTool, WorldControl
from .gate import SafetyGate
from .observe import TruthLeak, assemble
from .session import Session, SessionStore, context

_log = logging.getLogger(__name__)


class Turn:
    def __init__(self, store: SessionStore, session: Session, brain, body: RemoteBody | None,
                 world: WorldControl | None, tools: list[RemoteTool], gate: SafetyGate,
                 emit: Callable[[dict], None], max_steps: int = config.MAX_STEPS,
                 time_budget_s: float = config.TURN_TIME_BUDGET_S, world_name: str = "",
                 current: Callable[[], bool] | None = None, guard=None, control_token=None) -> None:
        self.store, self.session, self.brain = store, session, brain
        self.body, self.world, self.tool_nodes, self.gate = body, world, tools, gate
        self.emit = emit
        self.notes_max = config.NOTES_MAX
        self.max_steps = max_steps
        self.deadline = time.monotonic() + time_budget_s
        self.world_name = world_name
        self.step = 0
        self.done = False
        self._stop: str | None = None
        self._tools: list[ToolSpec] | None = None
        self._routes: dict[str, tuple[str, object, ToolSpec]] = {}   # name → (origin, node, spec)
        self.trace = {"inputs": [], "thinking": [], "reply": ""}
        self.control_token = control_token
        self._guard = guard or threading.RLock()
        self._current = current or (lambda: True)
        self._continuation: dict = {}
        self._observation: dict = {}
        self.run_id = uuid.uuid4().hex

    def is_current(self) -> bool:
        return (self._current() and not self._stop and not interrupt.is_set(self.session.id)
                and time.monotonic() < self.deadline
                and self.store.exists(self.session.id) and self.store.is_active(self.session.id))

    def memory(self):
        return self.store.memory(self.session.id)

    def raw_history(self) -> list[dict]:
        entries = self.memory().messages()
        return context.build(entries, token_budget=sum(len(str(m)) for m in entries) + 1)

    def model_phase(self, active):
        if self.is_current():
            self.emit({"type": "model_phase", "active": active,
                       "text": "Analyzing current observations" if active else "Model request finished"})

    def model_reply(self, reply) -> None:
        completed = getattr(reply, "finish_status", "completed") == "completed"
        self._continuation = getattr(reply, "continuation", {}) if completed else {}
        self.memory().append("model_response", {"run_id": self.run_id, "model": self.brain.model,
            "text": reply.text, "tool_calls": [dataclasses.asdict(call) for call in reply.tool_calls],
            "continuation": getattr(reply, "continuation", {}), "finish_status": reply.finish_status})
        self.memory().append("model_usage", {"model": self.brain.model,
                                             "usage": getattr(reply, "usage", None)})
        if completed and self.body and self._observation:
            self.body.post("/supervision", {"epoch": (self.body.health() or {}).get("epoch"),
                                           "frame_seq": self._observation.get("frame_seq")})

    def executing(self) -> bool:
        if not self.body:
            return False
        value = self.body.post("/executions/status")
        return bool(value.get("running"))

    def supervise_wait(self) -> None:
        end = time.monotonic() + config.SUPERVISION_WAIT_S
        while time.monotonic() < end and self.is_current():
            time.sleep(min(config.BRIDGE_WATCHDOG_POLL_S, end - time.monotonic()))

    # -- tool sheet -----------------------------------------------------------------------
    def tools(self) -> list[ToolSpec]:
        if self._tools is not None:
            return self._tools
        out: list[ToolSpec] = []
        self._routes = {}
        if self.body is not None:
            for t in self.body.capabilities().operations:
                spec = dataclasses.replace(t, description=trust.clip(t.description, config.TOOL_DESC_MAX_CHARS))
                out.append(spec)
                self._routes[t.name] = ("body", self.body, spec)
        for node in self.tool_nodes:
            try:
                caps = node.capabilities()
            except Exception as e:
                _log.warning("tool node %s unreachable: %s", node.name, e)
                continue
            for t in caps.operations:
                if t.name in self._routes:
                    _log.warning("tool %s from %s shadowed by the body; dropped", t.name, node.name)
                    continue
                spec = dataclasses.replace(t, description=trust.clip(t.description, config.TOOL_DESC_MAX_CHARS))
                out.append(spec)
                self._routes[t.name] = ("tool", node, spec)
        self._tools = out
        for td in (prompts.HISTORY_SEARCH_TOOL, prompts.HISTORY_READ_TOOL):
            spec = ToolSpec(td["name"], td["description"], td["parameters"], "read")
            if spec.name not in self._routes:
                self._tools.append(spec)
                self._routes[spec.name] = ("memory", self, spec)
        return out

    def system_prompt(self):
        from ..brain.react import ReactBrain
        return ReactBrain._system(self.brain, self)

    def model_tools(self):
        from ..brain.react import _meta_tools
        capabilities = self.tools()
        return capabilities if getattr(self, "domain_capabilities", False) else capabilities + _meta_tools({s.name for s in capabilities})

    def dispatch_decision(self, call):
        if call.name in prompts.META_TOOL_NAMES and not getattr(self, "domain_capabilities", False):
            return self.set_register(SetRegister(call.name, dict(call.arguments) | {"call_id": call.call_id}))
        return self.call_tool(call)

    def system_context(self) -> dict:
        self.session.armed = self.store.get(self.session.id).armed
        ctx: dict = {"body": None, "armed": self.session.armed}
        if self.body is None:
            return ctx
        caps = self.body.capabilities()
        ctx.update({"body": self.body.name, "family": caps.family, "world": self.world_name,
                    "has_tools": bool(caps.operations), "sensor_names": [i for i in caps.sensors] + list(self.session.sensors)})
        blocks = []
        if caps.guidance:
            blocks.append(("body", trust.fence(caps.guidance, config.GUIDANCE_MAX_CHARS)))
        for node in self.tool_nodes:
            try:
                g = node.capabilities().guidance
            except Exception:
                g = ""
            if g:
                blocks.append((f"tool `{node.name}`", trust.fence(g, config.GUIDANCE_MAX_CHARS)))
        ctx["guidance_blocks"] = blocks
        lines = []
        for o in (caps.config or {}).get("options") or []:
            if o.get("key") and o.get("key") != "armed":
                val = o.get("value")
                for c in o.get("choices") or []:
                    if c.get("value") == val:
                        val = c.get("label") or val
                lines.append(f"· {o.get('label') or o.get('key')}: {val}")
        ctx["config_lines"] = lines
        if self._observation:
            import json
            ctx["config_lines"].append("Current sensor state: " + json.dumps(self._observation, default=str))
        return ctx

    def history(self) -> list[dict]:
        return context.build(self.store.get(self.session.id).messages)

    def registers(self) -> dict:
        s = self.store.get(self.session.id)
        return {"core_task": s.core_task, "notes": list(s.notes)}

    # -- see --------------------------------------------------------------------------------
    def observe(self):
        self.step += 1
        if self.body is None:
            return None
        try:
            obs = assemble(self.body, self.world, self.session.sensors)
        except TruthLeak as e:
            _log.error("observation refused: %s", e)
            obs = self.body.perceive()
            obs.state["ambient_refused"] = str(e)
        required = (self.body.health() or {}).get("supervision_sensors", [])
        received = {image["name"] for image in obs.images if image.get("png")}
        with self._guard:
            if not self.is_current():
                return None
            if required and not set(required) <= received:
                self.body.post("/stop")
                raise RuntimeError("required visual supervision frames are unavailable")
        return self.record_observation(obs)

    def record_observation(self, obs):
        """Persist and publish sensor input without acquiring execution control."""
        with self._guard:
            if not self.is_current():
                return None
            self.store.append_perception(self.session.id, obs.state, obs.images)
            self._observation = dict(obs.state)
        first = obs.image_png
        b64 = base64.b64encode(first).decode() if first else None
        self.trace["inputs"].append({"state": obs.state, "n_images": len(obs.images)})
        self.emit({"type": op.EV_PERCEPTION, "image_b64": b64, "state": obs.state,
                   "n_images": len(obs.images), "cameras": [i["name"] for i in obs.images],
                   "images": [{"name": i["name"], "b64": base64.b64encode(i["png"]).decode()}
                              for i in obs.images]})
        return obs

    # -- think ------------------------------------------------------------------------------
    def call_identity(self, name: str) -> dict:
        """Optional application identity; legacy transport names remain unclassified."""
        return {}

    def think(self, msg: Think) -> None:
        if not self.is_current():
            return
        tcs = [{"id": c.call_id, "name": c.name, "arguments": c.arguments,
                **self.call_identity(c.name)} for c in msg.tool_calls]
        self.store.append(self.session.id, {"role": "assistant", "text": msg.text or "",
                                            "tool_calls": tcs, "brain": self.session.brain,
                                            "continuation": self._continuation})
        self._continuation = {}
        self.trace["thinking"].append({"text": msg.text or "", "tool_calls": tcs, "tool_results": []})
        if msg.text:
            self.emit({"type": op.EV_THINKING, "text": msg.text})

    # -- gate + act -------------------------------------------------------------------------
    def call_tool(self, call: CallTool) -> ToolResult:
        if not self.is_current():
            return ToolResult(call.call_id, call.name, False, "Run invalidated before dispatch")
        self.emit({"type": op.EV_TOOL_CALL, "name": call.name, "args": call.arguments})
        self.tools()
        route = self._routes.get(call.name)
        if route is None:
            res = ActionResult(False, prompts.UNKNOWN_TOOL_RESULT.format(name=call.name))
            return self._finish(call, res)
        origin, node, spec = route
        native_name = getattr(self, "_native_names", {}).get(call.name, call.name)
        if origin == "memory":
            try:
                if call.name == prompts.HISTORY_SEARCH_TOOL["name"]:
                    data = self.memory().search(str(call.arguments.get("query", "")),
                                                int(call.arguments.get("limit", 10)))
                else:
                    data = self.memory().read_range(int(call.arguments.get("start", 1)),
                                                    int(call.arguments.get("limit", 20)))
                    data = [{**e, "data": {k: v for k, v in e["data"].items()
                                            if k not in ("continuation",)}} for e in data
                            if e["kind"] == "message"]
                return self._finish(call, ActionResult(True, "Session history", {"events": data}))
            except (ValueError, TypeError) as error:
                return self._finish(call, ActionResult(False, str(error)))
        if origin == "body" and spec.kind in ("primitive", "skill"):
            required = (self.body.health() or {}).get("supervision_sensors", [])
            if required and not self._observation.get("frame_seq"):
                return self._finish(call, ActionResult(False, "Observe all required visual sensors before starting motion"))
        self.session.armed = self.store.get(self.session.id).armed
        ok, reason = self.gate.check(native_name, call.arguments, kind=spec.kind, origin=origin,
                                     armed=self.session.armed, spec=spec)
        self.emit({"type": op.EV_GATE, "name": call.name, "allowed": ok, "reason": reason})
        from .session import log
        log.record("gate", {"tool": call.name, "origin": origin, "kind": spec.kind,
                            "armed": self.session.armed, "allowed": ok, "reason": reason})
        if not ok:
            return self._finish(call, ActionResult(False, prompts.SAFETY_BLOCKED_RESULT.format(reason=reason)))

        q: queue.Queue = queue.Queue()
        holder: dict = {}

        def _on_progress(message, progress, total):
            q.put({"type": op.EV_PROGRESS, "name": call.name, "message": message or "", "progress": progress})

        def _aborted() -> bool:
            return not self.is_current()

        def _work():
            self.memory().flush()
            if not self.is_current():
                return
            extra = {"_control_token": self.control_token} if origin == "body" and self.control_token else {}
            holder["res"] = node.invoke(native_name, _on_progress=_on_progress, _should_abort=_aborted,
                                         **extra, **call.arguments)

        ctx = contextvars.copy_context()
        th = threading.Thread(target=ctx.run, args=(_work,), daemon=True)
        th.start()
        while th.is_alive():
            if not self.is_current():
                return ToolResult(call.call_id, call.name, False, "Run invalidated; result unknown")
            try:
                self.emit(q.get(timeout=config.BRIDGE_WATCHDOG_POLL_S))
            except queue.Empty:
                pass
        th.join()
        while not q.empty():
            self.emit(q.get())
        res: ActionResult = holder.get("res") or ActionResult(False, prompts.TOOL_THREAD_DIED_RESULT)
        return self._finish(call, res)

    def _finish(self, call: CallTool, res: ActionResult) -> ToolResult:
        if not self.is_current():
            return ToolResult.from_action(call.call_id, call.name, res)
        import json
        content = json.dumps({"message": res.message, "data": res.data}, default=str) if res.data else res.message
        self.store.append(self.session.id, {"role": "tool", "id": call.call_id, "name": call.name,
                                            "content": content, "data": res.data,
                                            **self.call_identity(call.name)})
        if self.trace["thinking"]:
            self.trace["thinking"][-1]["tool_results"].append({"name": call.name, "ok": res.ok,
                                                               "message": res.message})
        self.emit({"type": op.EV_TOOL_RESULT, "name": call.name, "ok": res.ok, "message": res.message})
        return ToolResult.from_action(call.call_id, call.name, res)

    # -- the brain's own registers ----------------------------------------------------------
    def set_register(self, msg: SetRegister) -> ToolResult:
        if not self.is_current():
            return ToolResult("", msg.kind, False, "Run invalidated")
        args = dict(msg.value or {})
        call_id = str(args.pop("call_id", "") or f"reg-{self.step}")
        sid = self.session.id
        s = self.store.get(sid)
        if msg.kind == prompts.CORE_TASK_SET_TOOL["name"]:
            task = str(args.get("task", "")).strip()
            if not task:
                res = ActionResult(False, prompts.CORE_TASK_EMPTY_REPLY)
            else:
                self.store.set_core_task(sid, task)
                res = ActionResult(True, prompts.CORE_TASK_SET_REPLY.format(task=task))
        elif msg.kind == prompts.CORE_TASK_CLEAR_TOOL["name"]:
            self.store.set_core_task(sid, "")
            res = ActionResult(True, prompts.CORE_TASK_CLEAR_REPLY)
        elif msg.kind == prompts.NOTE_ADD_TOOL["name"]:
            note = str(args.get("note", "")).strip()
            notes = list(s.notes)
            if not note:
                res = ActionResult(False, prompts.NOTE_EMPTY_REPLY)
            elif len(note) > config.NOTE_MAX_CHARS:
                res = ActionResult(False, prompts.NOTE_TOO_LONG_REPLY.format(n=len(note), limit=config.NOTE_MAX_CHARS))
            elif len(notes) >= self.notes_max:
                res = ActionResult(False, prompts.NOTE_FULL_REPLY.format(limit=self.notes_max))
            else:
                notes.append(note)
                self.store.set_notes(sid, notes)
                res = ActionResult(True, prompts.NOTE_ADD_REPLY.format(n=len(notes), note=note))
        elif msg.kind == prompts.NOTE_DROP_TOOL["name"]:
            notes = list(s.notes)
            try:
                n = int(args.get("number", 0))
            except (TypeError, ValueError):
                n = 0
            if not 1 <= n <= len(notes):
                res = ActionResult(False, prompts.NOTE_DROP_BAD_REPLY.format(n=n, total=len(notes)))
            else:
                dropped = notes.pop(n - 1)
                self.store.set_notes(sid, notes)
                res = ActionResult(True, prompts.NOTE_DROP_REPLY.format(n=n, note=dropped))
        else:
            res = ActionResult(False, prompts.UNKNOWN_TOOL_RESULT.format(name=msg.kind))
        return self._finish(CallTool(call_id, msg.kind, args), res)

    # -- say / stop ---------------------------------------------------------------------------
    def say(self, msg: Say) -> None:
        if not self.is_current():
            return
        self.store.append(self.session.id, {"role": "assistant", "text": msg.text or "",
                                            "brain": self.session.brain, "continuation": self._continuation})
        self._continuation = {}
        self.trace["reply"] = msg.text or ""
        self.emit({"type": op.EV_REPLY, "text": msg.text or ""})
        self.done = True

    def stop_reason(self) -> str | None:
        if not self._current():
            return "superseded"
        if self._stop:
            return self._stop
        reason = None
        if interrupt.is_set(self.session.id):
            reason = "interrupt"
        elif self.step >= self.max_steps:
            reason = "steps"
        elif time.monotonic() >= self.deadline:
            reason = "time"
        if reason:
            self._stop = reason
            if self.body and self._current():
                self.body.post("/stop")
            interrupt.clear(self.session.id)
            text = messages.STOP_REPLIES[reason]
            self.store.append(self.session.id, {"role": "assistant", "text": text, "brain": self.session.brain})
            self.trace["reply"] = text
            self.emit({"type": op.EV_REPLY, "text": text, "stop_reason": reason})
            self.done = True
        return reason

    # -- driver -------------------------------------------------------------------------------
    def run(self, user_text: str, *, recorded=False) -> dict:
        if not self._current() or not self.store.exists(self.session.id):
            return {"reply": "", "trace": self.trace, "brain": self.session.brain,
                    "model": self.brain.model, "stop_reason": "superseded"}
        if not recorded:
            self.store.append(self.session.id, {"role": "user", "text": user_text})
        self.memory().append("run_started", {"run_id": self.run_id, "model": self.brain.model})
        self.emit({"type": op.EV_START, "brain": self.session.brain, "model": self.brain.model})
        try:
            self.brain.run_turn(self, UserMessage(user_text))
        except Exception as e:
            _log.error("brain turn failed: %s", type(e).__name__)
            with self._guard:
                if self.body and self._current():
                    self.body.post("/stop")
            if isinstance(e, ContextOverflow):
                self.say(Say(messages.CONTEXT_BUDGET_REPLY))
            elif self.is_current():
                from ..vlm.errors import model_error
                error = model_error(e).public()
                self._stop = "model_error"
                self.store.append(self.session.id, {"role": "error", **error, "brain": self.session.brain})
                self.trace["reply"] = error["message"]
                self.trace["error"] = error
                self.emit({"type": "model_error", **error})
                self.done = True
        if not self.done:
            self.say(Say(""))
        self.emit({"type": op.EV_DONE})
        if self.store.exists(self.session.id):
            self.memory().append("run_ended", {"run_id": self.run_id, "stop": self._stop,
                                           "current": self._current()})
        return {"reply": self.trace["reply"], "trace": self.trace, "brain": self.session.brain,
                "model": self.brain.model, "stop_reason": self._stop, "error": self.trace.get("error")}


def _current_commit(method):
    def guarded(self, *args, **kwargs):
        with self._guard:
            if not self.is_current():
                return None
            return method(self, *args, **kwargs)
    return guarded


for _method in ("think", "say", "set_register", "_finish", "model_reply"):
    setattr(Turn, _method, _current_commit(getattr(Turn, _method)))


def _owned_stop(method):
    def stop(self):
        with self._guard:
            return method(self)
    return stop


Turn.stop_reason = _owned_stop(Turn.stop_reason)
