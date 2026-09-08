"""The hub: everything the CLI and the HTTP backend share.

Registry + launcher + sessions + gate + node clients, and the two operations that matter:
create a session (compatibility → launch → trust) and run a turn (epoch check → Turn).
"""
from __future__ import annotations

import queue
import threading
import time
from functools import wraps
from typing import Iterator

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
        self.scene_tests = SceneTests(self)

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
            b = brain_plugin.load(name)
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
                    sensors: list[str] | None = None, tools: list[str] | None = None) -> dict:
        if (body is None) != (world is None):
            raise ValueError("give both a body and a world, or neither (conversation only)")
        epoch = ""
        tool_names = list(tools) if tools is not None else list(self.registry.tools)
        if body and world:
            bspec, wspec = self.registry.body(body), self.registry.world(world)
            ok, reason = compat_check(wspec, bspec)
            if not ok:
                raise ValueError(reason)
            self.launcher.check_body_binding(bspec, wspec)
            wh = self.launcher.ensure_world(wspec, bspec)
            self.launcher.ensure_body(bspec, wspec, wh)
            wc = self.world_client(world, body)
            epoch = wc.epoch() if wc else ""
            offered = set(wc.sensors()) if wc else set()
            bad = [s for s in (sensors or []) if s not in offered]
            if bad:
                raise ValueError(f"world `{world}` does not publish sensor streams {bad}; it offers {sorted(offered)}")
        for t in tool_names:
            try:
                self.launcher.ensure_tool(self.registry.tool(t))
            except Exception as e:
                slog.record("operator", {"event": "tool_launch_failed", "tool": t, "error": str(e)})
                tool_names = [x for x in tool_names if x != t]
        s, frozen = self.store.new(brain, body, world, sensors=sensors, tools=tool_names, epoch=epoch)
        for sid in frozen:
            interrupt.request(sid)
            self.scene_tests.interrupt(sid)
        slog.record("operator", {"event": "session_new", "session": s.id, "brain": brain,
                                 "body": body, "world": world, "frozen": frozen})
        return s.summary()

    def arm(self, sid: str, armed: bool) -> dict:
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
        interrupt.request(sid)
        self.scene_tests.interrupt(sid)
        s = self.store.get(sid)
        if self._control_error(s):
            return
        if s.body:
            c = self.body_client(s.body)
            if c is not None:
                c.post("/stop")

    # -- operator control plane: emergency stop (hold the pose), release, reset --------------
    def _operator_note(self, sid: str, event: str, text: str, **fields) -> None:
        """Log it and put a line in the session, so the brain sees what the operator did."""
        slog.record("operator", {"event": event, "session": sid, **fields})
        self.store.append(sid, {"role": "assistant", "brain": "operator", "text": text, "tool_calls": []})

    def estop(self, sid: str) -> dict:
        """Hold the pose now: interrupt the turn, stop the skill, latch the joints. Never a power cut."""
        interrupt.request(sid)
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
        self.scene_tests.cancel(sid)
        wc = self.world_client(s.world, s.body)
        if wc is None:
            return {"ok": False, "message": f"the world `{s.world}` is not reachable"}
        res = wc.reset()
        body = None
        if res.get("ok"):
            c = self.body_client(s.body)
            body = c.release() if c is not None else None
            self._operator_note(sid, "reset_world", messages.RESET_NOTE, world=res, body=body)
        else:
            slog.record("operator", {"event": "reset_refused", "session": sid, "world": res})
        return {"ok": bool(res.get("ok")), "message": str(res.get("message", "")), "body": body}

    def _epoch_ok(self, s) -> bool:
        if not (s.body and s.world):
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

    # -- turns ------------------------------------------------------------------------------
    def _turn(self, sid: str, emit) -> Turn:
        s = self.store.get(sid)
        body = self.body_client(s.body) if s.body else None
        world = self.world_client(s.world, s.body) if (s.world and s.body) else None
        tools = [c for c in (self.tool_client(t) for t in s.tools) if c is not None]
        return Turn(self.store, s, self.brain(s.brain), body, world, tools, self.gate, emit,
                    world_name=s.world or "")

    def handle_stream(self, sid: str, text: str) -> Iterator[dict]:
        s = self.store.get(sid)
        if s.body and self.scene_tests.blocks(s.body):
            yield {"type": op.EV_REPLY, "text": "End scene testing before starting a robot task"}
            yield {"type": op.EV_DONE}
            return
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
        q: queue.Queue = queue.Queue()
        turn = self._turn(sid, q.put)

        def _run():
            with slog.session_scope(sid):
                try:
                    turn.run(text)
                finally:
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
        return {"reply": reply, "events": events, "stop_reason": stop}

    def tool_sheet(self, sid: str) -> list[dict]:
        """The tools the brain would see in this session, with origin and kind — for the remote control."""
        s = self.store.get(sid)
        if s.status != op.SESSION_ACTIVE:
            return []
        turn = self._turn(sid, lambda ev: None)
        out = []
        for spec in turn.tools():
            origin, node, _ = turn._routes[spec.name]
            out.append({"name": spec.name, "kind": spec.kind, "origin": origin, "node": node.name,
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
        q: queue.Queue = queue.Queue()
        turn = self._turn(sid, q.put)
        call_id = f"teleop-{int(time.time() * 1000)}"

        def _run():
            with slog.session_scope(sid):
                try:
                    interrupt.clear(sid)
                    turn.session.brain = "operator"
                    turn.think(Think(messages.TELEOP_STEP.format(name=name), [CallTool(call_id, name, arguments)]))
                    turn.call_tool(CallTool(call_id, name, arguments))
                finally:
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
        self.launcher.stop_all()
