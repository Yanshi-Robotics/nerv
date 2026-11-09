"""Model-free observation and action dispatcher. No provider or decision loop."""
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
from ..nerve.body import ActionResult, ToolSpec
from ..nerve.brain import CallTool, ToolResult
from ..nerve import operator as op
from ..platform import interrupt, trust
from ..platform.client import RemoteBody, RemoteTool, WorldControl
from ..platform.gate import SafetyGate
from ..platform.observe import TruthLeak, assemble
from ..platform.session import Session, SessionStore
_log = logging.getLogger(__name__)
class ExecutionTurn:
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

    def executing(self) -> bool:
        if not self.body:
            return False
        value = self.body.post("/executions/status")
        return bool(value.get("running"))

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
        return out

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

    def call_identity(self, name: str) -> dict:
        """Optional application identity; legacy transport names remain unclassified."""
        return {}

    def call_tool(self, call: CallTool) -> ToolResult:
        if not self.is_current():
            return ToolResult(call.call_id, call.name, False, "Run invalidated before dispatch")
        self.emit({"type": op.EV_TOOL_CALL, "name": call.name, "args": call.arguments})
        self.tools()
        route = self._routes.get(call.name)
        if route is None:
            res = ActionResult(False, f"Unknown action: {call.name}")
            return self._finish(call, res)
        origin, node, spec = route
        native_name = getattr(self, "_native_names", {}).get(call.name, call.name)
        if origin == "body" and spec.kind in ("primitive", "skill"):
            required = (self.body.health() or {}).get("supervision_sensors", [])
            if required and not self._observation.get("frame_seq"):
                return self._finish(call, ActionResult(False, "Observe all required visual sensors before starting motion"))
        self.session.armed = self.store.get(self.session.id).armed
        ok, reason = self.gate.check(native_name, call.arguments, kind=spec.kind, origin=origin,
                                     armed=self.session.armed, spec=spec)
        self.emit({"type": op.EV_GATE, "name": call.name, "allowed": ok, "reason": reason})
        from ..platform.session import log
        log.record("gate", {"tool": call.name, "origin": origin, "kind": spec.kind,
                            "armed": self.session.armed, "allowed": ok, "reason": reason})
        if not ok:
            return self._finish(call, ActionResult(False, f"Safety gate refused: {reason}"))

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
        res: ActionResult = holder.get("res") or ActionResult(False, "Action worker ended without a result")
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


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {},
            "required": list(required), "additionalProperties": False}


class ExternalExecution(ExecutionTurn):
    """Bounded commands supplied by an external Agent, never a model loop."""
    def __init__(self, hub, session, brain, emit, token, *, role=None, store=None):
        self.hub, self.token = hub, token
        self.robot = hub.body_client(session.body)
        super().__init__(store or hub.store, session, None, self.robot, None, [], hub.gate, emit,
                         current=lambda: not token.is_set(), control_token=token.control_token)
        self.external_current = lambda: False

    def is_current(self):
        return super().is_current() and self.external_current()

    def tools(self):
        tools = super().tools()
        return [tool for tool in tools if tool.name in self.token.allowed_actions]

    def call_tool(self, call):
        from jsonschema import validate
        self.tools()
        route = self._routes.get(call.name)
        if route is None or call.name not in self.token.allowed_actions:
            return self._finish(call, ActionResult(False, "Action is outside the selected control scheme"))
        try:
            validate(call.arguments, route[2].parameters)
            if not self.is_current():
                raise ValueError("Request was superseded")
            if self.token.action_budget <= 0:
                raise ValueError("Selected scheme action budget exhausted")
            previous = self.token.last_skill
            if route[2].kind == "skill" and previous not in (None, call.name):
                # An adapter must explicitly qualify each transition for this mode.
                transitions = (self.robot.capabilities().config or {}).get("skill_transitions", {})
                pair = [previous, call.name]
                if transitions.get("validated") is not True or pair not in transitions.get("pairs", []):
                    raise ValueError("Skill transition is unverified; operator intervention required")
            self.token.action_budget -= 1
            if route[2].kind == "skill":
                self.token.last_skill = call.name
            return super().call_tool(call)
        except Exception as error:
            return self._finish(call, ActionResult(False, str(error)))
