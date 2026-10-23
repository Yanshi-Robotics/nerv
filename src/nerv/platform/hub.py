"""The hub: everything the CLI and the HTTP backend share.

Registry + launcher + sessions + gate + node clients, and the two operations that matter:
create a session (compatibility → launch → trust) and run a turn (epoch check → Turn).
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
import hashlib
import json
from functools import wraps
from typing import Callable, Iterator
from types import SimpleNamespace

from .. import config
from ..brain import plugin as brain_plugin
from ..nerve import operator as op
from . import interrupt, messages
from .client import RemoteBody, RemoteTool, WorldControl
from .gate import SafetyGate
from .launcher import Launcher
from .registry import Registry
from .registry.compat import check as compat_check
from .router import Turn
from .session import SessionStore
from .session import log as slog
from .scene_tests import SceneTests


def _node_lifecycle(method):
    """Serialize launch/stop operations without delaying emergency controls."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._node_lock:
            return method(self, *args, **kwargs)
    return locked


class Nerv:
    def __init__(self, registry: Registry | None = None, store: SessionStore | None = None,
                 gate: SafetyGate | None = None, launcher: Launcher | None = None) -> None:
        self.registry = registry or Registry()
        self.store = store or SessionStore()
        self.gate = gate or SafetyGate()
        self.launcher = launcher or Launcher(self.registry)
        self._bodies: dict[str, RemoteBody] = {}
        self._worlds: dict[str, WorldControl] = {}
        self._tools: dict[str, RemoteTool] = {}
        self._brains: dict = {}
        self._node_lock = threading.RLock()
        self._arm_locks: dict[str, threading.RLock] = {}
        self._arm_locks_guard = threading.Lock()
        self.scene_tests = SceneTests(self)
        self._runs: dict[str, threading.Event] = {}
        self._runs_lock = threading.RLock()
        self._bound_runtime: tuple | None = None
        self._control_owner = uuid.uuid4().hex
        self._control_generation = 0
        for session in self.store.all():
            if session.body:
                self.store.set_armed(session.id, False)
                self.store.set_status(session.id, op.SESSION_RECONNECT)
            memory = self.store.memory(session.id)
            before = len(memory.events())
            memory.recover_runs()
            if len(memory.events()) > before:
                self.store.set_armed(session.id, False)
                if session.body:
                    self.store.set_status(session.id, op.SESSION_RECONNECT)
                self.store.append(session.id, {"role": "assistant", "brain": "operator",
                    "text": "Host restarted: unfinished execution outcome is unknown. No actions were replayed; reconnect and observe before any new task."})
        from .control_schemes import SchemeStore
        self.schemes = SchemeStore()
        from .robot_tasks import RobotTasks
        self.tasks = RobotTasks(self)
        from .world_controls import WorldControls
        self.world_controls = WorldControls(self)
        self._agent_serial: dict[str, threading.Lock] = {}

    def new_role_model(self, name, effort=..., frozen_spec=None):
        """Fresh role-local provider/continuation; fixtures may inject an explicit model."""
        from ..harness.agent import AgentHarness
        if name == "operator":
            raise ValueError("Select a visual model before starting an Agent task")
        if name in self._brains and not isinstance(self._brains[name], AgentHarness):
            return self._brains[name]
        if effort is ...:
            return brain_plugin.load(name, wrap=lambda llm: slog.LoggingLLM(llm, name))
        from ..vlm.store import ModelRegistry
        from ..harness.context import ContextManager
        from .. import config
        registry = ModelRegistry()
        if frozen_spec is None and not any(m["id"] == name and m["available"] for m in registry.list()):
            raise ValueError("Select an available visual model")
        llm = registry.provider(name, effort=effort, frozen_spec=frozen_spec)
        return AgentHarness(slog.LoggingLLM(llm, name), name,
                            ContextManager(min(config.REQUEST_TOKEN_BUDGET, getattr(llm, "context_window", None) or config.REQUEST_TOKEN_BUDGET),
                                           getattr(llm, "max_output_tokens", config.MAX_TOKENS),
                                           config.IMAGE_TOKEN_ALLOWANCE))

    def claim_robot(self, sid, *, allowed_actions=None, max_runtime_s=None, action_budget=None):
        session = self.store.get(sid)
        if error := self._control_error(session):
            raise ValueError(error["message"])
        with self.body_control_lock(session.body):
            self._control_generation += 1
            token = uuid.uuid4().hex
            client = self.body_client(session.body)
            if client is None:
                raise ValueError("Robot is unavailable")
            result = client.post("/control/claim", {"token": token, "owner": self._control_owner,
                                                   "generation": self._control_generation,
                                                   "allowed_actions": allowed_actions or [],
                                                   "max_runtime_s": max_runtime_s or config.TURN_TIME_BUDGET_S,
                                                   "action_budget": action_budget or 1})
            if not result.get("ok"):
                raise ValueError("Runtime refused control")
            return token

    def stop_robot(self, sid, expected_control=None):
        from .. import config
        session = self.store.get(sid)
        if error := self._control_error(session):
            return error
        client = self.body_client(session.body) if session.body else None
        if client is None:
            return {"ok": not session.body, "message": "Robot unavailable"}
        result = client.post("/stop", {"expected_control": expected_control} if expected_control else {})
        if not result.get("ok"):
            return result
        deadline = time.monotonic() + config.SCENE_ENTRY_TIMEOUT_S
        while time.monotonic() < deadline:
            status = client.post("/executions/status")
            if status.get("running") is False:
                return {"ok": True, "message": "Execution stopped; old dispatch revoked"}
            time.sleep(config.SCENE_ENTRY_POLL_S)
        return {"ok": False, "message": "Stop requested but execution termination is unconfirmed"}

    def stop_reply(self, sid):
        with self._runs_lock:
            if token := self._runs.get(sid):
                token.set()

    # -- node clients ---------------------------------------------------------------------
    def body_client(self, name: str) -> RemoteBody | None:
        h = self.launcher.nodes.get(f"body:{name}")
        if h is None:
            return None
        c = self._bodies.get(name)
        if c is None or c.base != h.url:
            c = RemoteBody(name, h.url)
            self._bodies[name] = c
        return c

    def world_client(self, world: str, body: str) -> WorldControl | None:
        h = self.launcher.nodes.get(f"world:{world}/{body}")
        if h is None:
            return None
        key = f"{world}/{body}"
        c = self._worlds.get(key)
        if c is None or c.base != h.url:
            c = WorldControl(key, h.url)
            self._worlds[key] = c
        return c

    def tool_client(self, name: str) -> RemoteTool | None:
        h = self.launcher.nodes.get(f"tool:{name}")
        if h is None:
            return None
        c = self._tools.get(name)
        if c is None or c.base != h.url:
            c = RemoteTool(name, h.url)
            self._tools[name] = c
        return c

    def node_client(self, key: str):
        kind, _, name = key.partition(":")
        if kind == "body":
            return self.body_client(name)
        if kind == "tool":
            return self.tool_client(name)
        return None

    def brain(self, name: str):
        b = self._brains.get(name)
        if b is None:
            b = brain_plugin.load(name, wrap=lambda llm: slog.LoggingLLM(llm, name))
            self._brains[name] = b
        return b

    @_node_lifecycle
    def stop_simulation(self, key: str, expected_world: str, expected_epoch: str,
                        session_id: str | None = None) -> dict:
        """Stop an owned body/world pair; retain every session and observation."""
        handle = self.launcher.nodes.get(key)
        if handle is None or handle.kind != "body":
            raise ValueError("select a running body node")
        body = key.partition(":")[2]
        world = handle.meta.get("world", "")
        if (not expected_epoch or world != expected_world or handle.meta.get("epoch") != expected_epoch):
            raise ValueError("the simulation changed; refresh the page before stopping it")
        if session_id:
            session = self.store.get(session_id)
            if session.body != body or session.world != world or self._control_error(session):
                raise ValueError("this session no longer controls the simulation; refresh the page")
        if not world or self.registry.world(world).kind != "sim":
            raise ValueError("only a local simulated body and world can be stopped here")
        world_key = f"world:{world}/{body}"
        pair = [key, world_key]
        for node_key in pair:
            node = self.launcher.nodes.get(node_key)
            if node is None or node.attached or node.proc is None:
                raise ValueError("cannot stop an incomplete or externally managed simulation")
        frozen = []
        for session in self.store.all():
            if session.body == body and (session.status != op.SESSION_FROZEN or session.armed):
                self.stop_reply(session.id)
                if token := self.tasks._tokens.get(session.id):
                    token.set()
                interrupt.request(session.id)
                session.status = op.SESSION_FROZEN
                session.armed = False
                self.store.save(session)
                self.scene_tests.interrupt(session.id)
                frozen.append(session.id)
        # Stop the command producer before physics, then discard cached clients.
        for node_key in pair:
            self.launcher.stop(node_key)
        self._bodies.pop(body, None)
        self._worlds.pop(f"{world}/{body}", None)
        self._bound_runtime = None
        for sid in frozen:
            if self.tasks._jobs.get(sid, {}).get("status") in {"starting", "running", "updating", "stopping"}:
                self.tasks._save(sid, status="cancelled", outcome="cancelled", message="Simulation instance ended")
        slog.record("operator", {"event": "simulation_stopped", "nodes": pair, "frozen": frozen})
        return {"ok": True, "stopped": pair, "frozen": frozen}

    def _control_error(self, s) -> dict | None:
        if s.status != op.SESSION_ACTIVE or not self._epoch_ok(s):
            status = self.store.get(s.id).status
            return {"ok": False, "message": messages.SESSION_NOT_ACTIVE.format(status=status)}
        return None

    # -- sessions ---------------------------------------------------------------------------
    @_node_lifecycle
    def new_session(self, brain: str, body: str | None, world: str | None,
                    sensors: list[str] | None = None, tools: list[str] | None = None,
                    mode: str = "sim", world_configuration: dict | None = None, *, operator_workspace=False) -> dict:
        if brain == "operator" and not operator_workspace:
            raise ValueError("Select a configured chat model; use the operator workspace for device operations")
        if body:
            from .robot_tasks import ACTIVE
            for previous in self.store.all():
                if previous.body == body and (self.tasks.snapshot(previous.id).get("task") or {}).get("status") in ACTIVE:
                    raise ValueError("Return to the running task before creating another robot session")
            from .module_jobs import catalog
            if any(job["state"] in ("queued", "running") for job in catalog()["jobs"]):
                raise ValueError("Wait for component installation before creating a robot instance")
        # Explicit injected test brains are not part of the production catalogue.
        from ..harness.agent import AgentHarness
        from ..vlm.store import ModelRegistry
        if brain != "operator" and (brain not in self._brains or isinstance(self._brains[brain], AgentHarness)):
            ModelRegistry().require_key(brain)
        if world and not body:
            raise ValueError("a simulated world requires a robot profile")
        if world:
            mode = self.registry.world(world).kind
        if body and mode == "sim" and not world:
            raise ValueError("select a simulation world")
        if world_configuration and mode != "sim":
            raise ValueError("World configuration is simulation-only")
        from ..world.configuration import resolve_configuration
        values = resolve_configuration(self.registry.world(world).configuration, world_configuration or {}) if world else {}
        if world and self._bound_runtime:
            existing = self.world_client(world, body)
            if existing and (existing.health() or {}).get("configuration", {}).get("configuration", {}) != values:
                raise ValueError("End the current instance before changing world configuration")
        binding = (body, mode, world)
        if body and self._bound_runtime and self._bound_runtime != binding:
            raise ValueError("this NERV instance is bound to one robot/environment; close the instance before selecting another")
        epoch = ""
        tool_names = list(tools) if tools is not None else list(self.registry.tools)
        if body and world:
            bspec, wspec = self.registry.body(body), self.registry.world(world)
            ok, reason = compat_check(wspec, bspec)
            if not ok:
                raise ValueError(reason)
            self.launcher.check_body_binding(bspec, wspec)
            old_nodes = set(self.launcher.nodes)
            try:
                wh = self.launcher.ensure_world(wspec, bspec, values)
                self.launcher.ensure_body(bspec, wspec, wh)
            except Exception:
                for key in list(self.launcher.nodes):
                    if key not in old_nodes:
                        self.launcher.stop(key)
                raise
            wc = self.world_client(world, body)
            epoch = wc.epoch() if wc else ""
            offered = set(wc.sensors()) if wc else set()
            bad = [s for s in (sensors or []) if s not in offered]
            if bad:
                raise ValueError(f"world `{world}` does not publish sensor streams {bad}; it offers {sorted(offered)}")
        elif body:
            spec = self.registry.body(body)
            if mode not in spec.buses:
                raise ValueError("this robot has no registered binding for the selected environment")
            handle = self.launcher.ensure_runtime(spec, mode)
            epoch = str(handle.meta.get("epoch", ""))
        if body:
            self._bound_runtime = binding
        previous = [s for s in self.store.all() if s.body and s.status == op.SESSION_ACTIVE]
        enabled = False  # Every new session requires a fresh operator motion authorization.
        for old in previous:
            self.stop(old.id)
        for t in tool_names:
            try:
                self.launcher.ensure_tool(self.registry.tool(t))
            except Exception as e:
                slog.record("operator", {"event": "tool_launch_failed", "tool": t, "error": str(e)})
                tool_names = [x for x in tool_names if x != t]
        s, frozen = self.store.new(brain, body, world, sensors=sensors, tools=tool_names, epoch=epoch)
        s.mode, s.binding, s.armed = mode, f"{body}:{mode}:{world or ''}" if body else "", enabled
        self.store.save(s)
        for sid in frozen:
            interrupt.request(sid)
            self.scene_tests.interrupt(sid)
        slog.record("operator", {"event": "session_new", "session": s.id, "brain": brain,
                                 "body": body, "world": world, "frozen": frozen})
        return s.summary()

    def body_control_lock(self, body: str):
        # Arming changes are ordered per body. Emergency stop and lease
        # interruption never take this lock or wait for a slow config request.
        with self._arm_locks_guard:
            return self._arm_locks.setdefault(body, threading.RLock())

    def arm(self, sid: str, armed: bool, *, _guard: Callable[[], None] | None = None) -> dict:
        with self.body_control_lock(self.store.get(sid).body):
            if _guard is not None:
                _guard()
            return self._set_armed(sid, armed)

    def _set_armed(self, sid: str, armed: bool) -> dict:
        s = self.store.get(sid)
        if error := self._control_error(s):
            return {**error, "armed": False}
        if not s.body:
            return {"ok": False, "armed": False, "message": "a conversation-only session has nothing to arm"}
        if armed and self.scene_tests.blocks(s.body):
            return {"ok": False, "armed": False, "message": "End scene testing before arming the body"}
        c = self.body_client(s.body)
        if c is None:
            return {"ok": False, "armed": False, "message": f"the body `{s.body}` is not reachable"}
        forwarded = c.set_config("armed", "true" if armed else "false")
        if not forwarded.get("ok"):
            return {"ok": False, "armed": s.armed, "body": forwarded,
                    "message": forwarded.get("message", "body refused the arming change")}
        self.store.set_armed(sid, armed)
        slog.record("operator", {"event": "arm", "session": sid, "armed": armed, "body": forwarded})
        return {"ok": True, "armed": armed, "body": forwarded}

    def stop(self, sid: str) -> None:
        self.stop_reply(sid)
        interrupt.request(sid)
        self.scene_tests.interrupt(sid)
        self.tasks.cancel(sid)

    # -- operator control plane: emergency stop (hold the pose), release, reset --------------
    def _operator_note(self, sid: str, event: str, text: str, **fields) -> None:
        """Log it and put a line in the session, so the brain sees what the operator did."""
        slog.record("operator", {"event": event, "session": sid, **fields})
        self.store.append(sid, {"role": "assistant", "brain": "operator", "text": text, "tool_calls": []})

    def estop(self, sid: str) -> dict:
        """Hold the pose now: interrupt the turn, stop the skill, latch the joints. Never a power cut."""
        with self._runs_lock:
            if sid in self._runs:
                self._runs[sid].set()
        interrupt.request(sid)
        with self.tasks._lock:
            self.tasks._generation[sid] = self.tasks._generation.get(sid, 0) + 1
            if token := self.tasks._tokens.get(sid):
                token.set()
        self.scene_tests.interrupt(sid)
        s = self.store.get(sid)
        if error := self._control_error(s):
            return error
        if not s.body:
            return {"ok": False, "held": False, "message": "a conversation-only session has no body to stop"}
        c = self.body_client(s.body)
        if c is None:
            return {"ok": False, "held": False, "message": f"the body `{s.body}` is not reachable"}
        res = c.hold("operator")
        self.store.set_armed(sid, False)
        self.store.set_core_task(sid, "")
        if sid in self.tasks._jobs:
            self.tasks._save(sid, status="cancelled" if res.get("ok") else "failed",
                             outcome="cancelled" if res.get("ok") else "unknown", message="Emergency stop", stop_result=res)
        self._operator_note(sid, "estop", messages.ESTOP_NOTE, body=res)
        return {"ok": bool(res.get("ok")), "held": bool(res.get("held", res.get("ok"))),
                "message": str(res.get("message", ""))}

    def release(self, sid: str) -> dict:
        s = self.store.get(sid)
        if error := self._control_error(s):
            return error
        if not s.body:
            return {"ok": False, "held": False, "message": "a conversation-only session has no body"}
        c = self.body_client(s.body)
        if c is None:
            return {"ok": False, "held": True, "message": f"the body `{s.body}` is not reachable"}
        res = c.release()
        if res.get("ok"):
            self.store.set_armed(sid, False)
            self.store.set_core_task(sid, "")
            self._operator_note(sid, "release", messages.RELEASE_NOTE, body=res)
        else:
            slog.record("operator", {"event": "release_refused", "session": sid, "body": res})
        return {"ok": bool(res.get("ok")), "held": bool(res.get("held", not res.get("ok"))),
                "message": str(res.get("message", ""))}

    def reset_world(self, sid: str) -> dict:
        """Reset the entire simulated scene, including passive objects, preserving its time phase."""
        interrupt.request(sid)
        s = self.store.get(sid)
        if error := self._control_error(s):
            return error
        if not (s.body and s.world):
            return {"ok": False, "message": "this session has no world to reset"}
        if s.mode != "sim":
            return {"ok": False, "message": "World control is simulation-only"}
        self.tasks.cancel(sid, reason="World reset", strict=True)
        self.scene_tests.cancel(sid)
        wc = self.world_client(s.world, s.body)
        if wc is None:
            return {"ok": False, "message": f"the world `{s.world}` is not reachable"}
        descriptor = wc.control_get()
        res = wc.control_post({"instance_id": s.epoch, "revision": descriptor["revision"],
                               "request_id": uuid.uuid4().hex, "command": "reset", "arguments": {}})
        body = None
        if res.get("ok"):
            c = self.body_client(s.body)
            body = c.release() if c is not None else None
            self.store.set_armed(sid, False)
            if c:
                c.set_config("armed", "false")
            self._operator_note(sid, "reset_world", messages.RESET_NOTE, world=res, body=body)
        else:
            slog.record("operator", {"event": "reset_refused", "session": sid, "world": res})
        return {"ok": bool(res.get("ok")), "message": str(res.get("message", "")), "body": body}

    def _epoch_ok(self, s) -> bool:
        if not s.body:
            return True
        if not s.world:
            client = self.body_client(s.body)
            now = (client.health() or {}).get("epoch", "") if client else ""
            if not now or (s.epoch and now != s.epoch):
                self.store.set_status(s.id, op.SESSION_RECONNECT)
                self.store.set_armed(s.id, False)
                return False
            return True
        handle = self.launcher.nodes.get(f"body:{s.body}")
        if handle and handle.meta.get("world") and handle.meta["world"] != s.world:
            self.store.set_status(s.id, op.SESSION_RECONNECT)
            self.store.set_armed(s.id, False)
            return False
        wc = self.world_client(s.world, s.body)
        if wc is None:
            return True
        now = wc.epoch()
        if s.epoch and now and now != s.epoch:
            self.store.set_status(s.id, op.SESSION_RECONNECT)
            self.store.set_armed(s.id, False)
            slog.record("operator", {"event": "epoch_changed", "session": s.id, "was": s.epoch, "now": now})
            return False
        return True

    def binding_signature(self):
        if not self._bound_runtime:
            return None
        body, mode, world = self._bound_runtime
        client = self.body_client(body)
        health, state = (client.health(), client.status()) if client else (None, None)
        if not health or not state or (mode == "real" and not state.get("prepared")):
            return None
        payload = {"profile": self.registry.body(body).model_dump(), "mode": mode, "world": world,
                   "epoch": health.get("epoch"), "preparation": state.get("preparation", {}),
                   "runtime_manifest": health.get("runtime_manifest", {}),
                   "artifacts": [{k: v for k, v in policy.items() if k != "loaded"}
                                 for policy in state.get("policies", [])]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    # -- turns ------------------------------------------------------------------------------
    def _turn(self, sid: str, emit, token=None, operator=False) -> Turn:
        s = self.store.get(sid)
        body = self.body_client(s.body) if s.body else None
        world = self.world_client(s.world, s.body) if (s.world and s.body) else None
        tools = [c for c in (self.tool_client(t) for t in s.agent_tools) if c is not None]
        brain = SimpleNamespace(name="operator", model="operator", vision=False) if operator else self.new_role_model(s.brain)
        if not operator and isinstance(brain, __import__("nerv.harness.agent", fromlist=["AgentHarness"]).AgentHarness):
            from .role_turn import RoleTurn
            from .user_settings import snapshot
            limits = snapshot()
            brain.context_manager.token_budget = min(limits["request_token_budget"], getattr(brain.llm, "context_window", None) or limits["request_token_budget"])
            turn = RoleTurn(self, s, brain, emit, token or threading.Event())
            turn.max_steps = limits["max_steps"]
            turn.deadline = time.monotonic() + limits["turn_time_budget_s"]
            turn.notes_max = limits["notes_max"]
            return turn
        return Turn(self.store, s, brain, body, world, tools, self.gate, emit,
                    world_name=s.world or "", current=(lambda: not token.is_set()) if token else None, guard=self._runs_lock, control_token=getattr(token, "control_token", None))

    def _claim_run(self, sid, session, robot=True):
        with self._runs_lock:
            if old := self._runs.get(sid):
                old.set()
            token = threading.Event()
            self._runs[sid] = token
            token.control_token = uuid.uuid4().hex
            self._control_generation += 1
            if body := self.body_client(session.body) if session.body and robot else None:
                claimed = body.post("/control/claim", {"token": token.control_token, "owner": self._control_owner,
                                                      "generation": self._control_generation,
                                                   "allowed_actions": self.schemes.get(session.control_scheme, session.control_scheme_revision).actions if session.control_scheme else [],
                                                      "max_runtime_s": config.TURN_TIME_BUDGET_S,
                                                      "action_budget": config.MAX_STEPS})
                if not claimed.get("ok"):
                    token.set()
                    raise RuntimeError("runtime refused the control generation")
            interrupt.clear(sid)
        return token

    def handle_stream(self, sid: str, text: str) -> Iterator[dict]:
        s = self.store.get(sid)
        if s.status != op.SESSION_ACTIVE:
            yield {"type": op.EV_REPLY, "text": messages.SESSION_NOT_ACTIVE.format(status=s.status)}
            yield {"type": op.EV_DONE}
            return
        if not self._epoch_ok(s):
            yield {"type": op.EV_REPLY, "text": messages.SESSION_NOT_ACTIVE.format(status=op.SESSION_RECONNECT)}
            yield {"type": op.EV_DONE}
            return
        try:
            self.brain(s.brain)
        except KeyError:
            yield {"type": op.EV_REPLY, "text": messages.UNKNOWN_BRAIN_REPLY}
            yield {"type": op.EV_DONE}
            return
        from ..harness.agent import AgentHarness
        self.store.append(sid, {"role": "user", "text": text})
        token = self._claim_run(sid, s, robot=not isinstance(self.brain(s.brain), AgentHarness))
        q: queue.Queue = queue.Queue()
        turn = self._turn(sid, q.put, token)

        def _run():
            with slog.session_scope(sid), slog.role_scope("agent"):
                try:
                    with self._agent_serial.setdefault(sid, threading.Lock()):
                        turn.run(text, recorded=True)
                finally:
                    with self._runs_lock:
                        if self._runs.get(sid) is token:
                            self._runs.pop(sid, None)
                    q.put(None)

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        while True:
            ev = q.get()
            if ev is None:
                break
            yield ev
        th.join()

    def handle(self, sid: str, text: str) -> dict:
        events = list(self.handle_stream(sid, text))
        reply = ""
        stop = None
        for ev in events:
            if ev.get("type") == op.EV_REPLY:
                reply = ev.get("text", "")
                stop = ev.get("stop_reason")
        error = next(({"code": ev["code"], "message": ev["message"]} for ev in events if ev.get("type") == "model_error"), None)
        return {"reply": reply, "events": events, "stop_reason": stop, "error": error}

    def tool_sheet(self, sid: str) -> list[dict]:
        """The tools the brain would see in this session, with origin and kind — for the remote control."""
        s = self.store.get(sid)
        if s.status != op.SESSION_ACTIVE:
            return []
        turn = self._turn(sid, lambda ev: None, operator=True)
        out = []
        for spec in turn.tools():
            origin, node, _ = turn._routes[spec.name]
            out.append({"name": spec.name, "kind": spec.kind, "domain": "robot_actions" if origin == "body" else "agent_tools", "origin": origin, "node": getattr(node, "name", "session-memory"),
                        "description": spec.description, "parameters": spec.parameters})
        return out

    def teleop_stream(self, sid: str, name: str, arguments: dict) -> Iterator[dict]:
        """The operator calls one tool directly. Same gate, same nodes, same log as the brain —
        recorded in the session as a step taken by the operator so the brain sees it next turn."""
        from ..nerve.brain import CallTool, Think
        s = self.store.get(sid)
        if s.body and self.scene_tests.blocks(s.body):
            yield {"type": op.EV_TOOL_RESULT, "name": name, "ok": False,
                   "message": "End scene testing before moving the body"}
            yield {"type": op.EV_DONE}
            return
        if s.status != op.SESSION_ACTIVE:
            yield {"type": op.EV_TOOL_RESULT, "name": name, "ok": False,
                   "message": messages.SESSION_NOT_ACTIVE.format(status=s.status)}
            yield {"type": op.EV_DONE}
            return
        if not self._epoch_ok(s):
            yield {"type": op.EV_TOOL_RESULT, "name": name, "ok": False,
                   "message": messages.SESSION_NOT_ACTIVE.format(status=op.SESSION_RECONNECT)}
            yield {"type": op.EV_DONE}
            return
        token = threading.Event()
        q: queue.Queue = queue.Queue()
        def manual_event(event):
            if event.get("type") in (op.EV_TOOL_CALL, op.EV_TOOL_RESULT, op.EV_PROGRESS):
                self.store.memory(sid).append("robot_activity", {
                    "instance_id": s.epoch, "task_id": None, "execution_id": getattr(token, "control_token", None),
                    "version": None, "source": "system1", "type": event["type"],
                    "name": event.get("name"), "ok": event.get("ok"),
                    "text": event.get("message") or event.get("name", "")})
            q.put(event)
        turn = self._turn(sid, manual_event, token, operator=True)
        turn.tools()
        route = turn._routes.get(name)
        if route and route[0] == "body" and route[2].kind != "read":
            stopped = self.tasks.cancel(sid, reason="Manual control takeover")
            if not stopped.get("ok"):
                yield {"type": op.EV_TOOL_RESULT, "name": name, "ok": False, "message": stopped.get("message", "")}
                yield {"type": op.EV_DONE}
                return
            token.control_token = self.claim_robot(sid, allowed_actions=[route[2].name])
            turn.control_token = token.control_token
        call_id = f"teleop-{int(time.time() * 1000)}"

        def _run():
            with slog.session_scope(sid):
                try:
                    interrupt.clear(sid)
                    turn.session.brain = "operator"
                    if route and route[0] == "body" and route[2].kind in ("primitive", "skill"):
                        turn.observe()
                    turn.think(Think(messages.TELEOP_STEP.format(name=name), [CallTool(call_id, name, arguments)]))
                    turn.call_tool(CallTool(call_id, name, arguments))
                except Exception as error:
                    q.put({"type": op.EV_TOOL_RESULT, "name": name, "ok": False, "message": str(error)})
                finally:
                    with self._runs_lock:
                        if self._runs.get(sid) is token:
                            self._runs.pop(sid, None)
                    q.put(None)

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        yield {"type": op.EV_START, "brain": "operator", "model": "teleop"}
        while True:
            ev = q.get()
            if ev is None:
                break
            yield ev
        th.join()
        yield {"type": op.EV_DONE}

    def perceive(self, sid: str):
        s = self.store.get(sid)
        if not s.body or self._control_error(s):
            return None
        from .observe import assemble
        observation = assemble(self.body_client(s.body), self.world_client(s.world, s.body) if s.world else None,
                               s.sensors)
        # A stopped/rebound session must not receive an in-flight observation.
        return None if self._control_error(self.store.get(sid)) else observation

    def shutdown(self) -> None:
        for token in self.tasks._tokens.values():
            token.set()
        self.launcher.stop_all()
