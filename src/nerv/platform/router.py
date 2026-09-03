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
from typing import Callable

from .. import config
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
                 time_budget_s: float = config.TURN_TIME_BUDGET_S, world_name: str = "") -> None:
        self.store, self.session, self.brain = store, session, brain
        self.body, self.world, self.tool_nodes, self.gate = body, world, tools, gate
        self.emit = emit
        self.max_steps = max_steps
        self.deadline = time.monotonic() + time_budget_s
        self.world_name = world_name
        self.step = 0
        self.done = False
        self._stop: str | None = None
        self._tools: list[ToolSpec] | None = None
        self._routes: dict[str, tuple[str, object, ToolSpec]] = {}   # name → (origin, node, spec)
        self.trace = {"inputs": [], "thinking": [], "reply": ""}

    # -- tool sheet -----------------------------------------------------------------------
    def tools(self) -> list[ToolSpec]:
        if self._tools is not None:
            return self._tools
        out: list[ToolSpec] = []
        self._routes = {}
        if self.body is not None:
            for t in self.body.capabilities().tools:
                spec = dataclasses.replace(t, description=trust.clip(t.description, config.TOOL_DESC_MAX_CHARS))
                out.append(spec)
                self._routes[t.name] = ("body", self.body, spec)
        for node in self.tool_nodes:
            try:
                caps = node.capabilities()
            except Exception as e:
                _log.warning("tool node %s unreachable: %s", node.name, e)
                continue
            for t in caps.tools:
                if t.name in self._routes:
                    _log.warning("tool %s from %s shadowed by the body; dropped", t.name, node.name)
                    continue
                spec = dataclasses.replace(t, description=trust.clip(t.description, config.TOOL_DESC_MAX_CHARS))
                out.append(spec)
                self._routes[t.name] = ("tool", node, spec)
        self._tools = out
        return out

    def system_context(self) -> dict:
        ctx: dict = {"body": None, "armed": self.session.armed}
        if self.body is None:
            return ctx
        caps = self.body.capabilities()
        ctx.update({"body": self.body.name, "family": caps.family, "world": self.world_name,
                    "has_tools": bool(caps.tools), "sensor_names": [i for i in caps.sensors] + list(self.session.sensors)})
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
        self.store.append_perception(self.session.id, obs.state, obs.images)
        first = obs.image_png
        b64 = base64.b64encode(first).decode() if first else None
        self.trace["inputs"].append({"state": obs.state, "n_images": len(obs.images)})
        self.emit({"type": op.EV_PERCEPTION, "image_b64": b64, "state": obs.state,
                   "n_images": len(obs.images), "cameras": [i["name"] for i in obs.images],
                   "images": [{"name": i["name"], "b64": base64.b64encode(i["png"]).decode()}
                              for i in obs.images]})
        return obs

    # -- think ------------------------------------------------------------------------------
    def think(self, msg: Think) -> None:
        tcs = [{"id": c.call_id, "name": c.name, "arguments": c.arguments} for c in msg.tool_calls]
        self.store.append(self.session.id, {"role": "assistant", "text": msg.text or "",
                                            "tool_calls": tcs, "brain": self.session.brain})
        self.trace["thinking"].append({"text": msg.text or "", "tool_calls": tcs, "tool_results": []})
        if msg.text:
            self.emit({"type": op.EV_THINKING, "text": msg.text})

    # -- gate + act -------------------------------------------------------------------------
    def call_tool(self, call: CallTool) -> ToolResult:
        self.emit({"type": op.EV_TOOL_CALL, "name": call.name, "args": call.arguments})
        self.tools()
        route = self._routes.get(call.name)
        if route is None:
            res = ActionResult(False, prompts.UNKNOWN_TOOL_RESULT.format(name=call.name))
            return self._finish(call, res)
        origin, node, spec = route
        ok, reason = self.gate.check(call.name, call.arguments, kind=spec.kind, origin=origin,
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
            return interrupt.is_set(self.session.id)

        def _work():
            holder["res"] = node.invoke(call.name, _on_progress=_on_progress, _should_abort=_aborted,
                                        **call.arguments)

        ctx = contextvars.copy_context()
        th = threading.Thread(target=ctx.run, args=(_work,), daemon=True)
        th.start()
        while th.is_alive():
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
        self.store.append(self.session.id, {"role": "tool", "id": call.call_id, "name": call.name,
                                            "content": res.message, "data": res.data})
        if self.trace["thinking"]:
            self.trace["thinking"][-1]["tool_results"].append({"name": call.name, "ok": res.ok,
                                                               "message": res.message})
        self.emit({"type": op.EV_TOOL_RESULT, "name": call.name, "ok": res.ok, "message": res.message})
        return ToolResult.from_action(call.call_id, call.name, res)

    # -- the brain's own registers ----------------------------------------------------------
    def set_register(self, msg: SetRegister) -> ToolResult:
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
            elif len(notes) >= config.NOTES_MAX:
                res = ActionResult(False, prompts.NOTE_FULL_REPLY.format(limit=config.NOTES_MAX))
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
        self.store.append(self.session.id, {"role": "assistant", "text": msg.text or "",
                                            "brain": self.session.brain})
        self.trace["reply"] = msg.text or ""
        self.emit({"type": op.EV_REPLY, "text": msg.text or ""})
        self.done = True

    def stop_reason(self) -> str | None:
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
            interrupt.clear(self.session.id)
            text = messages.STOP_REPLIES[reason]
            self.store.append(self.session.id, {"role": "assistant", "text": text, "brain": self.session.brain})
            self.trace["reply"] = text
            self.emit({"type": op.EV_REPLY, "text": text, "stop_reason": reason})
            self.done = True
        return reason

    # -- driver -------------------------------------------------------------------------------
    def run(self, user_text: str) -> dict:
        self.store.append(self.session.id, {"role": "user", "text": user_text})
        interrupt.clear(self.session.id)
        self.emit({"type": op.EV_START, "brain": self.session.brain, "model": self.brain.model})
        try:
            self.brain.run_turn(self, UserMessage(user_text))
        except Exception as e:
            _log.exception("brain turn failed")
            self.say(Say(messages.BRAIN_CALL_FAILED_REPLY.format(error=f"{type(e).__name__}: {e}")))
        if not self.done:
            self.say(Say(""))
        self.emit({"type": op.EV_DONE})
        return {"reply": self.trace["reply"], "trace": self.trace, "brain": self.session.brain,
                "model": self.brain.model, "stop_reason": self._stop}
