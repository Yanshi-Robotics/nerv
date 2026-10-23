"""Journaled tasks using the operator-selected execution scheme."""
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
            source = "system2" if kind in ("thinking", "reply", "perception", "start", "model_phase", "model_error") else "system1"
            # No image payloads or provider reasoning are sent to activity bubbles.
            payload = {"task_id": task_id, "version": version,
                       "instance_id": state["instance_id"], "source": source,
                       "execution_id": event.get("execution_id"), "type": kind, "text": event.get("text") or event.get("message", ""),
                       "name": event.get("name", ""), "ok": False if kind == "model_error" else event.get("ok"),
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
            from .control_schemes import resolve
            if not session.control_scheme:
                raise ValueError("Select a control scheme before starting a task")
            scheme = resolve(self.hub, session, self.hub.schemes.get(session.control_scheme, session.control_scheme_revision))
            model = scheme["decision_model"]
            brain = self.hub.new_role_model(model, scheme["effort"]) if model else None
            spec = getattr(getattr(brain, "llm", None), "spec", None)
            state = self._save(sid, model_config=spec.model_dump() if spec else None, task_id="task_" + uuid.uuid4().hex, instance_id=session.epoch,
                               goal=goal, constraints=constraints, version=1, status="starting",
                               model=model, effort=scheme["effort"], scheme=scheme, outcome=None, message="Task accepted")
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
                scheme = state["scheme"]
                token.allowed_actions = frozenset(scheme["actions"])
                token.action_budget = scheme["action_budget"]
                token.last_skill = None
                token.control_token = self.hub.claim_robot(sid, allowed_actions=scheme["actions"],
                    max_runtime_s=scheme["max_runtime_s"], action_budget=scheme["action_budget"])
            except Exception as error:
                self._save(sid, status="failed", outcome="not_started", message=str(error))
                return
            self._tokens[sid] = token
        if brain is None:
            return self._launch_fixed(sid, state, token)
        store = TaskStore(self.hub.store, sid, state["task_id"], state["model"], state["goal"])
        turn = RoleTurn(self.hub, store.get(sid), brain,
                        lambda event: self.event(sid, state["task_id"], state["version"], event),
                        token, role="system2", store=store)
        turn.deadline = time.monotonic() + state["scheme"]["max_runtime_s"]

        def work():
            try:
                with self._lock:
                    if token.is_set() or self._tokens.get(sid) is not token:
                        return
                    self._save(sid, status="running", message="Decision layer is observing and planning")
                from .session import log
                with log.session_scope(sid), log.role_scope("system2", state["task_id"]):
                    outcome = turn.run(state["goal"] + "\nConstraints: " + state["constraints"])
                if not token.is_set():
                    # Finishing narration is not an independent task-success judgement.
                    stopped = self._stop_execution(sid, token)
                    with self._lock:
                        if not token.is_set() and self._tokens.get(sid) is token:
                            self._save(sid, status="finished" if stopped.get("ok") and not outcome.get("error") else "failed",
                                       outcome="model_report" if stopped.get("ok") and not outcome.get("error") else "unknown",
                                       message=outcome.get("reply", "") or "Task controller ended",
                                       stop_result=stopped)
            except Exception as error:
                if not token.is_set():
                    self._stop_execution(sid, token)
                    with self._lock:
                        if not token.is_set() and self._tokens.get(sid) is token:
                            self._save(sid, status="failed", outcome="unknown", message=str(error))
        thread = threading.Thread(target=work, name=state["task_id"], daemon=True)
        with self._lock:
            self._threads[sid] = thread
        thread.start()

    def _stop_execution(self, sid, token):
        try:
            result = self.hub.stop_robot(sid, expected_control=token.control_token)
        except Exception as error:
            result = {"ok": False, "message": str(error)}
        with self._lock:
            if not result.get("ok") and self._tokens.get(sid) is token:
                # A terminal journal entry is not confirmation that hardware stopped.
                # Only a confirmed operator stop or a new instance clears this block.
                self._stopping.add(sid)
        return result

    def _launch_fixed(self, sid, state, token):
        from .router import Turn
        from ..nerve.brain import CallTool
        session = self.hub.store.get(sid)
        robot = self.hub.body_client(session.body)
        # No provider, role model or synthetic model identity is created.
        store = TaskStore(self.hub.store, sid, state["task_id"], "operator", state["goal"])
        turn = Turn(store, store.get(sid), None, robot, None, [], self.hub.gate,
                    lambda event: self.event(sid, state["task_id"], state["version"], event),
                    time_budget_s=state["scheme"]["max_runtime_s"],
                    current=lambda: not token.is_set(), control_token=token.control_token)

        def work():
            try:
                with self._lock:
                    if token.is_set() or self._tokens.get(sid) is not token:
                        return
                    self._save(sid, status="running", message="Fixed policy is running; operator supervision required")
                from . import interrupt
                interrupt.clear(sid)
                turn.tools()
                native = state["scheme"]["actions"][0]
                wire = next(name for name, route in turn._routes.items() if route[0] == "body" and route[2].name == native)
                turn.observe()
                result = turn.call_tool(CallTool(uuid.uuid4().hex, wire, {}))
                if not result.ok:
                    raise RuntimeError(result.message)
                execution = robot.post("/executions/status")
                self._save(sid, execution_id=execution.get("execution_id"))
                from .. import config
                while execution.get("running") and turn.is_current():
                    token.wait(config.BRIDGE_WATCHDOG_POLL_S)
                    execution = robot.post("/executions/status")
                if not token.is_set():
                    stopped = self._stop_execution(sid, token)
                    with self._lock:
                        if not token.is_set() and self._tokens.get(sid) is token:
                            self._save(sid, status="finished" if stopped.get("ok") and execution.get("state") != "failed" else "failed",
                                       outcome="unknown", execution=execution, stop_result=stopped,
                                       end_reason="time_limit" if time.monotonic() >= turn.deadline else execution.get("state", "execution_ended"),
                                       message="Policy execution ended; task success has not been verified")
            except Exception as error:
                if not token.is_set():
                    stopped = self._stop_execution(sid, token)
                    with self._lock:
                        if not token.is_set() and self._tokens.get(sid) is token:
                            self._save(sid, status="failed", outcome="unknown", message=str(error), stop_result=stopped)
        thread = threading.Thread(target=work, name=state["task_id"], daemon=True)
        self._threads[sid] = thread
        thread.start()

    def select_scheme(self, sid, scheme_id, revision=None):
        with self._operations:
            if self._jobs.get(sid, {}).get("status") in ACTIVE or sid in self._stopping:
                raise ValueError("Stop the task and confirm termination before switching schemes")
            session = self.hub.store.get(sid)
            if session.status != "active":
                raise ValueError("Select an active session")
            scheme = self.hub.schemes.get(scheme_id, revision)
            if scheme.robot != session.body or scheme.mode != session.mode:
                raise ValueError("Scheme belongs to a different robot or environment")
            session.control_scheme, session.control_scheme_revision = scheme.id, scheme.revision
            self.hub.store.save(session)
            return session.summary()

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
            if state["scheme"]["template"] == "fixed_policy":
                raise ValueError("A fixed policy cannot be retargeted; stop it before starting again")
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
