"""Independent, journaled System 2 jobs; the browser is never their supervisor."""
from __future__ import annotations

import threading
import time
import uuid

from .role_turn import RoleTurn, object_schema
from .task_store import TaskStore

ACTIVE = frozenset({"starting", "running", "updating", "stopping"})
TERMINAL = frozenset({"finished", "cancelled", "failed", "interrupted"})


class RobotTasks:
    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._jobs = {}
        self._tokens = {}
        self._threads = {}
        self._generation = {}
        self._stopping = set()
        for session in hub.store.all():
            states = [e["data"] for e in hub.store.memory(session.id).events() if e["kind"] == "robot_task"]
            if states:
                state = dict(states[-1])
                self._jobs[session.id] = state
                if state["status"] in ACTIVE:
                    self._save(session.id, status="interrupted", outcome="unknown",
                               message="Host restarted; no task or action was replayed.")

    @staticmethod
    def capabilities():
        text = {"type": "string", "minLength": 1}
        version = {"type": "integer", "minimum": 1}
        return [
            ("start", "Start one independently supervised robot task.",
             object_schema({"goal": text, "constraints": {"type": "string"}}, ["goal"])),
            ("status", "Read current robot task progress without interrupting it.", object_schema()),
            ("update", "Stop the old execution and revise the current task. Does not retarget a fixed policy.",
             object_schema({"goal": text, "constraints": {"type": "string"}, "expected_version": version},
                           ["expected_version"])),
            ("cancel", "Cancel the task locally without waiting for a model response.", object_schema()),
        ]

    def _save(self, sid, **fields):
        with self._lock:
            state = {**self._jobs.get(sid, {}), **fields, "updated_at": time.time()}
            # Durable before it becomes visible or may authorize an action.
            self.hub.store.memory(sid).append("robot_task", state)
            self._jobs[sid] = state
            return dict(state)

    def event(self, sid, task_id, version, event):
        with self._lock:
            state = self._jobs.get(sid, {})
            if (state.get("task_id") != task_id or state.get("version") != version
                    or state.get("status") not in {"starting", "running", "updating"}):
                return
            kind = event.get("type", "")
            source = "system2" if kind in ("thinking", "reply", "perception", "start", "model_phase") else "system1"
            # No image payloads or provider reasoning are sent to activity bubbles.
            payload = {"task_id": task_id, "version": version,
                       "instance_id": state["instance_id"], "source": source,
                       "execution_id": event.get("execution_id"), "type": kind, "text": event.get("text") or event.get("message", ""),
                       "name": event.get("name", ""), "ok": event.get("ok"),
                       "observation": event.get("state", {}).get("frame_seq")}
            self.hub.store.memory(sid).append("robot_activity", payload)

    def snapshot(self, sid, after=0):
        with self._lock:
            state = dict(self._jobs.get(sid, {}))
        events = self.hub.store.memory(sid).events()
        activity = [{"source": "host", "type": e["kind"], **e["data"], "seq": e["seq"], "time": e["time"]}
                    for e in events if e["seq"] > after and e["kind"] in ("robot_activity", "robot_task")]
        return {"ok": True, "task": state or None, "events": activity,
                "cursor": events[-1]["seq"] if events else 0}

    def invoke(self, sid, operation, arguments, current=lambda: True):
        if not current():
            raise ValueError("Agent decision superseded")
        if operation == "status":
            return self.snapshot(sid)
        if operation == "start":
            return self.start(sid, current=current, **arguments)
        if operation == "update":
            return self.update(sid, current=current, **arguments)
        if operation == "cancel":
            return self.cancel(sid)
        raise ValueError("unknown robot task operation")

    def start(self, sid, goal, constraints="", current=lambda: True):
        with self._operations:
            session = self.hub.store.get(sid)
            if not session.body or self.hub._control_error(session):
                raise ValueError("An active robot binding is required")
            if not goal.strip():
                raise ValueError("A task goal is required")
            if self.hub.scene_tests.blocks(session.body):
                raise ValueError("End scene testing before starting a task")
            if self._jobs.get(sid, {}).get("status") in ACTIVE:
                raise ValueError("Update or cancel the existing task first")
            with self._lock:
                if sid in self._stopping:
                    raise ValueError("Robot is still stopping")
                generation = self._generation.get(sid, 0)
            model = session.task_model or session.brain
            brain = self.hub.new_role_model(model, session.task_effort)
            spec = getattr(getattr(brain, "llm", None), "spec", None)
            state = self._save(sid, model_config=spec.model_dump() if spec else None, task_id="task_" + uuid.uuid4().hex, instance_id=session.epoch,
                               goal=goal, constraints=constraints, version=1, status="starting",
                               model=model, effort=session.task_effort, outcome=None, message="Task accepted")
            self._launch(sid, state, brain, generation, current)
            state = dict(self._jobs[sid])
            return {"ok": state["status"] not in {"cancelled", "failed"}, "message": state["message"], "data": state}

    def _launch(self, sid, state, brain, generation, current=lambda: True):
        token = threading.Event()
        with self._lock:
            if not current() or generation != self._generation.get(sid, 0) or sid in self._stopping:
                self._save(sid, status="cancelled", outcome="cancelled", message="Launch cancelled")
                return
            try:
                token.control_token = self.hub.claim_robot(sid)
            except Exception as error:
                self._save(sid, status="failed", outcome="not_started", message=str(error))
                return
            self._tokens[sid] = token
        store = TaskStore(self.hub.store, sid, state["task_id"], state["model"], state["goal"])
        turn = RoleTurn(self.hub, store.get(sid), brain,
                        lambda event: self.event(sid, state["task_id"], state["version"], event),
                        token, role="system2", store=store)

        def work():
            try:
                with self._lock:
                    if token.is_set() or self._tokens.get(sid) is not token:
                        return
                    self._save(sid, status="running", message="System 2 is observing and planning")
                from .session import log
                with log.session_scope(sid), log.role_scope("system2", state["task_id"]):
                    outcome = turn.run(state["goal"] + "\nConstraints: " + state["constraints"])
                if not token.is_set():
                    # Finishing narration is not an independent task-success judgement.
                    stopped = self.hub.stop_robot(sid, expected_control=token.control_token)
                    with self._lock:
                        if not token.is_set() and self._tokens.get(sid) is token:
                            self._save(sid, status="finished" if stopped.get("ok") else "failed",
                                       outcome="model_report" if stopped.get("ok") else "unknown",
                                       message=outcome.get("reply", "") or "Task controller ended",
                                       stop_result=stopped)
            except Exception as error:
                if not token.is_set():
                    self.hub.stop_robot(sid, expected_control=token.control_token)
                    with self._lock:
                        if not token.is_set() and self._tokens.get(sid) is token:
                            self._save(sid, status="failed", outcome="unknown", message=str(error))
        thread = threading.Thread(target=work, name=state["task_id"], daemon=True)
        with self._lock:
            self._threads[sid] = thread
        thread.start()

    def cancel(self, sid, reason="Task cancelled", strict=False):
        # Stop does not acquire the operation lock: it must preempt slow starts/model loads.
        with self._lock:
            self._generation[sid] = self._generation.get(sid, 0) + 1
            generation = self._generation[sid]
            self._stopping.add(sid)
            state = dict(self._jobs.get(sid, {}))
            token = self._tokens.get(sid)
            if token:
                token.set()
            active = state.get("status") in ACTIVE
            if active:
                self._save(sid, status="stopping", message=reason)
        try:
            result = self.hub.stop_robot(sid)
        except Exception as error:
            result = {"ok": False, "message": str(error)}
        with self._lock:
            if generation == self._generation[sid]:
                if result.get("ok"):
                    self._stopping.discard(sid)
                if active and self._jobs.get(sid, {}).get("task_id") == state.get("task_id"):
                    self._save(sid, status="cancelled" if result.get("ok") else "failed",
                               outcome="cancelled" if result.get("ok") else "unknown",
                               message=reason if result.get("ok") else result.get("message", "Stop not confirmed"),
                               stop_result=result)
        if strict and not result.get("ok"):
            raise ValueError("Robot stop could not be confirmed")
        return {**result, "task": self._jobs.get(sid)}

    def update(self, sid, expected_version, goal=None, constraints=None, current=lambda: True):
        with self._operations:
            state = dict(self._jobs.get(sid, {}))
            if not state or state["version"] != expected_version or state["status"] not in ACTIVE:
                raise ValueError("Task version changed or the task is no longer active")
            if goal is not None and not goal.strip():
                raise ValueError("A task goal is required")
            self.cancel(sid, reason="Stopping for task update", strict=True)
            with self._lock:
                generation = self._generation[sid]
            brain = (self.hub.new_role_model(state["model"], state["effort"], frozen_spec=state["model_config"])
                     if state.get("model_config") else self.hub.new_role_model(state["model"], state["effort"]))
            state = self._save(sid, goal=goal if goal is not None else state["goal"],
                               constraints=constraints if constraints is not None else state["constraints"],
                               version=expected_version + 1, status="updating", message="Task update applied")
            self._launch(sid, state, brain, generation, current)
            state = dict(self._jobs[sid])
            return {"ok": state["status"] not in {"cancelled", "failed"}, "message": state["message"], "data": state}
