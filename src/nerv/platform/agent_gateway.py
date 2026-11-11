"""Authenticated external-agent access to existing Host services.

Pairing and control assignment are operator-only methods, never MCP tools. Sensor
receipts attest delivery and client acknowledgement, not model understanding.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import threading
import time
import uuid

from jsonschema import validate

from .. import paths
from ..install_system import atomic_json


def _object(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 2048}
IDENT = {"type": "string", "minLength": 1, "maxLength": 128}
PROJECT = {"project_id": IDENT}
REQUEST = {**PROJECT, "request_id": IDENT}
OPERATIONS = {
    "projects_list": ("List projects explicitly shared with this connection.", _object()),
    "project_get": ("Read project configuration and current run.", _object(PROJECT, PROJECT)),
    "capabilities": ("Read permitted robot actions and supervision requirements.", _object(PROJECT, PROJECT)),
    "run_start": ("Start a simulation run in standby. Never connect real hardware.", _object(REQUEST, REQUEST)),
    "run_end": ("End the owned simulation run; does not disconnect real devices.", _object(REQUEST, REQUEST)),
    "observe": ("Read sensor images. Delivery is not acknowledgement of understanding.", _object(PROJECT, PROJECT)),
    "acknowledge": ("Confirm processing of a delivered observation before motion. Never acknowledge unseen frames.",
                    _object({**PROJECT, "receipt_id": IDENT}, ["project_id", "receipt_id"])),
    "task_start": ("Start the operator-selected scheme with current acknowledged observation.",
                   _object({**REQUEST, "goal": TEXT, "constraints": {"type": "string", "maxLength": 4096}},
                           ["project_id", "request_id", "goal"])),
    "task_status": ("Read the current task without changing it.", _object(PROJECT, PROJECT)),
    "task_cancel": ("Stop the task locally. A failed result means stopping is unconfirmed.", _object(REQUEST, REQUEST)),
    "task_update": ("Retired update operation: stop the task and start a new task with a fresh goal.",
                    _object({**REQUEST, "goal": TEXT, "expected_version": {"type": "integer", "minimum": 1}},
                            ["project_id", "request_id", "goal", "expected_version"])),
    "action": ("Execute one allowed action in the current external task, then observe again.",
               _object({**REQUEST, "task_id": IDENT, "version": {"type": "integer", "minimum": 1},
                        "action": IDENT, "arguments": {"type": "object"}},
                       ["project_id", "request_id", "task_id", "version", "action", "arguments"])),
    "events": ("Read bounded task events; polling does not renew supervision.",
               _object({**PROJECT, "after": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "wait_s": {"type": "number", "minimum": 0, "maximum": 20}}, ["project_id"])),
    "activity": ("Display a brief attributed statement, not a device result or system message.",
                 _object({**REQUEST, "text": TEXT}, ["project_id", "request_id", "text"])),
}
CONTROL_OPERATIONS = frozenset({"run_start", "run_end", "task_start", "task_update", "task_cancel", "action"})
IDEMPOTENT_OPERATIONS = CONTROL_OPERATIONS | {"activity"}
EVENT_FIELDS = frozenset({"type", "text", "message", "ok", "status", "outcome", "task_id", "version", "instance_id",
                          "execution_id", "end_reason", "source", "client_id", "client_name", "supervisor",
                          "brain_instance_id", "run_id", "robot_instance_id", "world_instance_id",
                          "binding_generation", "world_generation", "control_generation", "robot_epoch", "world_epoch"})


class AgentGateway:
    def __init__(self, hub, projects, root=None):
        self.hub, self.projects = hub, projects
        self.root = Path(root or Path(paths.NERV_HOME) / "agent-connections")
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._controls = {}  # Deliberately not restored after Host restart.
        self._receipts = {}
        self._leases = {}
        self._seen = {}
        self._expired = set()
        self._control_generations = {}
        self._active_actions = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="nerv-agent-supervision", daemon=True)
        self._thread.start()

    def _path(self, client_id):
        if not isinstance(client_id, str) or not client_id.startswith("agent_") or not client_id[6:].isalnum():
            raise ValueError("Invalid connection ID")
        return self.root / f"{client_id}.json"

    @staticmethod
    def _public(record):
        return {key: value for key, value in record.items() if key != "token_hash"}

    def list(self):
        result = []
        for path in sorted(self.root.glob("agent_*.json")):
            record = json.loads(path.read_text())
            result.append({**self._public(record), "last_seen": self._seen.get(record["id"])})
        return result

    def create(self, name, projects, permissions=None, *, allow_empty=False):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("Choose a connection name of 1 to 120 characters")
        if not isinstance(projects, list) or (not projects and not allow_empty) or len(set(projects)) != len(projects):
            raise ValueError("Select distinct projects to share")
        for project in projects:
            self.projects.get(project)
        permissions = permissions if permissions is not None else ["observe"]
        if not isinstance(permissions, list) or "observe" not in permissions or set(permissions) - {"observe", "control"}:
            raise ValueError("Connection permissions must include observe and may include control")
        client_id = "agent_" + uuid.uuid4().hex
        token = client_id + "." + secrets.token_urlsafe(32)
        record = {"id": client_id, "name": name.strip(), "projects": projects, "permissions": sorted(set(permissions)),
                  "created_at": time.time(), "revoked": False,
                  "token_hash": hashlib.sha256(token.encode()).hexdigest()}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        with self._lock:
            atomic_json(self._path(client_id), record)
            self._path(client_id).chmod(0o600)
            credential = self.root / "clients" / f"{client_id}.json"
            credential.parent.mkdir(mode=0o700, exist_ok=True)
            atomic_json(credential, {"client_id": client_id, "token": token})
            credential.chmod(0o600)
        return {"connection": self._public(record), "token": token}

    def _authenticate(self, token):
        if not isinstance(token, str) or len(token) > 512:
            raise PermissionError("Invalid or revoked Agent credential")
        try:
            record = json.loads(self._path(token.split(".", 1)[0]).read_text())
        except (ValueError, OSError):
            raise PermissionError("Invalid or revoked Agent credential") from None
        if record.get("revoked") or not hmac.compare_digest(record["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise PermissionError("Invalid or revoked Agent credential")
        self._seen[record["id"]] = time.time()
        return record

    def revoke(self, client_id):
        with self._lock:
            path = self._path(client_id)
            record = json.loads(path.read_text())
            record["revoked"] = True
            atomic_json(path, record)
            projects = [pid for pid in list(self._controls) if self.control(pid).get("owner_id") == client_id]
        results = [self.set_control(pid, accepting=False, owner_id=None) for pid in projects]
        return {"ok": all(r.get("ok", False) for r in results), "connection": self._public(record), "stops": results}

    def _run_identity(self, project_id):
        run = self.projects.active_run(project_id)
        return (run.get("id"), run.get("session_id")) if run else None

    def _invalidate_control(self, project_id):
        self._control_generations[project_id] = self._control_generations.get(project_id, 0) + 1
        self._controls[project_id] = {"accepting": False, "owner_id": None, "control_generation": self._control_generations[project_id]}
        self._leases.pop(project_id, None)
        self._expired.discard(project_id)
        for key in list(self._receipts):
            if key[1] == project_id:
                self._receipts.pop(key)

    def control(self, project_id):
        with self._lock:
            binding = self._controls.get(project_id, {}).get("run_identity")
            if binding is not None and binding != self._run_identity(project_id):
                self._invalidate_control(project_id)
            value = dict(self._controls.get(project_id, {"accepting": False, "owner_id": None}))
            lease = self._leases.get(project_id)
            value["supervision"] = ({"acknowledged_at": lease["acknowledged_at"],
                                      "expires_at": lease["expires_at"], "task_id": lease.get("task_id")}
                                     if lease else None)
            receipt = self._receipts.get((value.get("owner_id"), project_id), {})
            value["last_observation_at"] = receipt.get("delivered_at")
            value["last_acknowledged_at"] = lease.get("acknowledged_at") if lease else None
            value["supervision_state"] = ("confirmed" if lease and time.monotonic() < lease["deadline"]
                                           else "expired" if project_id in self._expired else "not_confirmed")
            return value

    def set_control(self, project_id, *, accepting, owner_id=None):
        """Operator-only: revoke first, then stop before assigning another owner."""
        self.projects.get(project_id)
        if accepting:
            if owner_id is None:
                raise ValueError("Explicitly select the controlling Agent")
            record = json.loads(self._path(owner_id).read_text())
            if record.get("revoked") or project_id not in record["projects"] or "control" not in record["permissions"]:
                raise ValueError("Selected connection does not have project control permission")
        with self._lock:
            self._invalidate_control(project_id)
        run = self.projects.active_run(project_id)
        identity = (run.get("id"), run.get("session_id")) if run else None
        if accepting and not identity:
            raise ValueError("Start a Run before assigning Agent control")
        if run:
            session = self.projects.require_session(project_id)
            result = self.hub.tasks.cancel(session.id, reason="Operator changed external control")
            if not result.get("ok"):
                return {"ok": False, "message": "Stop is unconfirmed; control remains disabled", "stop": result,
                        **self.control(project_id)}
        with self._lock:
            if accepting and identity != self._run_identity(project_id):
                raise ValueError("Run changed during control transfer; review and assign control again")
            self._controls[project_id] = {"accepting": bool(accepting), "owner_id": owner_id if accepting else None,
                                          "run_identity": identity if accepting else None,
                                          "control_generation": self._control_generations[project_id]}
        return {"ok": True, **self.control(project_id)}

    def pause(self, project_id):
        """Stop accepting new commands without asserting the current execution stopped."""
        self.projects.get(project_id)
        with self._lock:
            self._controls.setdefault(project_id, {"owner_id": None})["accepting"] = False
        return {"ok": True, **self.control(project_id)}

    def _owns(self, record, project_id, *, allow_paused=False):
        control = self.control(project_id)
        if (control.get("owner_id") != record["id"] or
                (not allow_paused and not control.get("accepting"))):
            raise PermissionError("The operator has not assigned this Agent active control")

    def _supervision_contract(self, session):
        robot = self.hub.body_client(session.body)
        if robot is None:
            raise ValueError("Robot is unavailable")
        contract = (robot.capabilities().config or {}).get("external_supervision", {})
        if contract.get("validated") is not True:
            raise ValueError("This runtime has no validated external supervision contract; observation only")
        for key in ("timeout_s", "max_observation_age_s"):
            value = contract.get(key)
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid external supervision contract")
        return robot, contract

    def _fresh(self, record, project_id, session):
        self._owns(record, project_id)
        lease = self._leases.get(project_id)
        if (not lease or lease["owner_id"] != record["id"] or lease["epoch"] != session.epoch
                or time.monotonic() >= lease["deadline"]):
            raise ValueError("Observe and acknowledge a fresh frame before requesting motion")
        return lease

    def invoke(self, token, operation, args):
        if self._stop.is_set():
            raise ValueError("NERV is shutting down")
        record = self._authenticate(token)
        if operation not in OPERATIONS:
            raise ValueError("Unknown external Agent operation")
        validate(args, OPERATIONS[operation][1])
        if operation == "projects_list":
            return {"projects": [self.projects.get(pid) for pid in record["projects"]]}
        project_id = args["project_id"]
        if project_id not in record["projects"]:
            raise PermissionError("Project is outside this connection's scope")
        if operation in CONTROL_OPERATIONS and "control" not in record["permissions"]:
            raise PermissionError("This connection has observation permission only")
        if operation in IDEMPOTENT_OPERATIONS:
            # Durable intent precedes side effects. Unknown outcomes never replay.
            key = hashlib.sha256((record["id"] + ":" + args["request_id"]).encode()).hexdigest()
            path = self.root / "requests" / f"{key}.json"
            fingerprint = hashlib.sha256(json.dumps([operation, args], sort_keys=True, allow_nan=False).encode()).hexdigest()
            with self._operations:
                if path.exists():
                    existing = json.loads(path.read_text())
                    if existing["fingerprint"] != fingerprint:
                        raise ValueError("Request ID was already used with different arguments")
                    if existing["status"] != "complete":
                        raise ValueError("Request outcome is unknown; inspect task state before a new request")
                    return existing["result"]
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomic_json(path, {"fingerprint": fingerprint, "status": "pending"})
            result = self._dispatch(record, operation, args)
            with self._operations:
                atomic_json(path, {"fingerprint": fingerprint, "status": "complete", "result": result})
            return result
        return self._dispatch(record, operation, args)

    def _dispatch(self, record, operation, args):
        pid = args["project_id"]
        if operation == "project_get":
            return {"project": self.projects.get(pid), "control": self.control(pid)}
        if operation == "run_start":
            return self.projects.create_run(pid, operator=False)
        session = self.projects.require_session(pid)
        if operation in {"task_start", "task_update", "task_cancel", "action", "run_end"}:
            self.hub.store.memory(session.id).append("external_agent_activity", {
                "source": "external_agent", "client_id": record["id"], "client_name": record["name"],
                "instance_id": session.epoch,
                **{key:getattr(session,key, None) for key in ("brain_instance_id","run_id","robot_instance_id","world_instance_id","binding_generation","world_generation","robot_epoch")},
                "world_epoch":session.epoch, "control_generation":self.control(pid).get("control_generation"),
                "type": "command_request", "text": args.get("goal") or args.get("action") or operation,
                "operation": operation, "request_id": args["request_id"]})
        if operation == "capabilities":
            robot = self.hub.body_client(session.body)
            if robot is None:
                return {"actions": [], "connected": False}
            caps = robot.capabilities()
            return {"actions": [{"name": op.name, "kind": op.kind, "description": op.description,
                                  "parameters": op.parameters} for op in caps.operations],
                    "sensors": caps.sensors, "external_supervision": (caps.config or {}).get("external_supervision"),
                    "control": self.control(pid)}
        if operation == "observe":
            control_generation = self.control(pid).get('control_generation')
            world_generation = getattr(session, 'world_generation', 0)
            robot = self.hub.body_client(session.body)
            body_epoch = (robot.health() or {}).get("epoch") if robot else None
            observed = self.hub.perceive(session.id)
            if observed is None:
                raise ValueError("No observation is available for this run")
            if not body_epoch or (robot.health() or {}).get("epoch") != body_epoch:
                raise ValueError("Runtime changed while collecting observations; observe again")
            receipt = {"id": "obs_" + uuid.uuid4().hex, "epoch": session.epoch, "body_epoch": body_epoch,
                       "state": dict(observed.state),
                       "delivered_at": time.time(), "delivered_mono": time.monotonic(),
                       "images": [i["name"] for i in observed.images if i.get("png")], "acknowledged": False,
                       "control_generation": control_generation}
            with self._lock:
                current_session = self.hub.store.get(session.id)
                if (self.control(pid).get('control_generation') != control_generation
                        or getattr(current_session, 'world_generation', 0) != world_generation):
                    raise ValueError('Control or World changed while collecting observations; observe again')
                self._receipts[(record["id"], pid)] = receipt
            self.hub.store.append_perception(session.id, observed.state, observed.images)
            return {"receipt_id": receipt["id"], "instance_id": session.epoch,
                    "delivered_at": receipt["delivered_at"], "state": observed.state,
                    "images": [{"name": i["name"], "mimeType": "image/png", "data": base64.b64encode(i["png"]).decode()}
                               for i in observed.images if i.get("png")]}
        if operation == "acknowledge":
            self._owns(record, pid, allow_paused=True)
            robot, contract = self._supervision_contract(session)
            with self._lock:
                receipt = self._receipts.get((record["id"], pid))
                generation = self.control(pid).get("control_generation")
                if (not receipt or receipt["id"] != args["receipt_id"] or receipt["epoch"] != session.epoch
                        or receipt["acknowledged"] or receipt.get("confirming")
                        or receipt.get("control_generation") != generation):
                    raise ValueError("Unknown, consumed or old-instance observation receipt")
                if time.monotonic() - receipt["delivered_mono"] > contract["max_observation_age_s"]:
                    raise ValueError("Observation receipt expired")
                receipt["confirming"] = True
            # Never hold the global state lock while waiting on a Runtime. A slow
            # camera/acknowledgement cannot delay another robot's stop or watchdog.
            try:
                state = receipt["state"]
                health = robot.health() or {}
                if health.get("epoch") != receipt["body_epoch"]:
                    raise ValueError("Runtime instance changed; observe again")
                required = health.get("supervision_sensors", [])
                if not receipt["images"] or not set(required) <= set(receipt["images"]) or not state.get("frame_seq"):
                    raise ValueError("Required visual sensor frames are unavailable")
                result = robot.post("/supervision", {"epoch": receipt["body_epoch"], "frame_seq": state["frame_seq"]})
                if not result.get("ok"):
                    raise ValueError("Runtime rejected stale or incompatible supervision")
                with self._lock:
                    control = self.control(pid)
                    if (control.get("owner_id") != record["id"] or control.get("control_generation") != generation
                            or self._receipts.get((record["id"], pid)) is not receipt
                            or time.monotonic() - receipt["delivered_mono"] > contract["max_observation_age_s"]):
                        raise ValueError("Control or observation changed during acknowledgement; observe again")
                    receipt["acknowledged"] = True
                    previous = self._leases.get(pid, {})
                    self._expired.discard(pid)
                    self._leases[pid] = {"owner_id": record["id"], "epoch": session.epoch, "session_id": session.id,
                                         "state": state, "control_generation": generation, "acknowledged_at": time.time(),
                                         "expires_at": time.time() + contract["timeout_s"],
                                         "deadline": time.monotonic() + contract["timeout_s"],
                                         "action_consumed": False, "task_id": previous.get("task_id")}
            finally:
                with self._lock:
                    receipt.pop("confirming", None)
            return {"ok": True, "receipt_id": receipt["id"], "meaning": "client_confirmed_processing", **self.control(pid)}
        if operation == "task_status":
            snapshot = self.hub.tasks.snapshot(session.id)
            return {"task": self._task_public(snapshot.get("task")), "control": self.control(pid)}
        if operation == "events":
            return self._events(record, pid, session.id, args)
        if operation == "activity":
            event = {"source": "external_agent", "client_id": record["id"], "client_name": record["name"],
                     "instance_id": session.epoch,
                     **{key: getattr(session, key, None) for key in ("brain_instance_id", "run_id", "robot_instance_id", "world_instance_id", "binding_generation", "world_generation", "robot_epoch")},
                     "world_epoch": session.epoch, "control_generation": self.control(pid).get("control_generation"),
                     "type": "agent_statement", "text": args["text"]}
            self.hub.store.memory(session.id).append("external_agent_activity", event)
            return {"ok": True}
        if operation == "task_cancel":
            self._owns(record, pid, allow_paused=True)
            return self.hub.tasks.cancel(session.id, reason="External Agent requested stop")
        if operation == "run_end":
            self._owns(record, pid, allow_paused=True)
            if session.mode != "sim":
                raise PermissionError("Real device lifecycle belongs to the operator")
            return self.projects.stop_run(pid, session.run_id)
        lease = self._fresh(record, pid, session)
        if operation == "task_start":
            result = self.hub.tasks.start(session.id, args["goal"], args.get("constraints", ""),
                                          current=lambda: self._still_current(record, pid, lease),
                                          supervisor={"kind": "external_agent", "client_id": record["id"],
                                                      "control_generation":lease["control_generation"]})
            if result.get("ok"):
                with self._lock:
                    latest = self._leases.get(pid, {})
                    if latest.get("owner_id") == record["id"] and latest.get("epoch") == session.epoch:
                        latest["task_id"] = result["data"]["task_id"]
            if result.get("ok") and not self._still_current(record, pid, lease):
                self.hub.tasks.cancel(session.id, reason="External supervision changed during task start")
            return result
        if operation == "task_update":
            return self.hub.tasks.update(session.id, args["expected_version"], goal=args["goal"],
                                          current=lambda: self._still_current(record, pid, lease))
        if operation == "action":
            with self._lock:
                if lease["action_consumed"]:
                    raise ValueError("Observe and acknowledge again after each action")
                lease["action_consumed"] = True
                self._active_actions.add(pid)
            try:
                return self.hub.tasks.external_action(session.id, args["task_id"], args["version"], args["action"],
                                                       args["arguments"], lease["state"],
                                                       current=lambda: self._still_current(record, pid, lease))
            finally:
                with self._lock:
                    self._active_actions.discard(pid)
        raise ValueError("Unsupported operation")

    def _still_current(self, record, pid, lease):
        with self._lock:
            control = self.control(pid)
            latest = self._leases.get(pid, {})
            return (not self._stop.is_set() and control.get("owner_id") == record["id"] and
                    control.get("accepting", False) and latest.get("owner_id") == record["id"] and
                    control.get("control_generation") == lease.get("control_generation") == latest.get("control_generation") and
                    latest.get("epoch") == lease["epoch"] and time.monotonic() < latest.get("deadline", 0))

    @staticmethod
    def _task_public(task):
        return {key: value for key, value in (task or {}).items() if key in EVENT_FIELDS}

    def _events(self, record, pid, sid, args):
        deadline = time.monotonic() + args.get("wait_s", 0)
        after = args.get("after", 0)
        while True:
            if json.loads(self._path(record["id"]).read_text()).get("revoked"):
                raise PermissionError("Connection was revoked")
            rows = self.hub.store.memory(sid).events()
            events = []
            cursor = after
            for row in rows:
                if row["seq"] <= after:
                    continue
                cursor = row["seq"]
                if row["kind"] not in {"robot_task", "robot_activity", "external_agent_activity"}:
                    continue
                data = row["data"]
                if data.get("type") in {"thinking", "model_phase", "reply"}:
                    continue  # Never forward model reasoning or private conversation.
                events.append({"seq": row["seq"], "time": row["time"], "kind": row["kind"],
                               "control_generation":data.get("control_generation"),
                               **{k: v for k, v in data.items() if k in EVENT_FIELDS}})
                if len(events) >= args.get("limit", 50):
                    break
            if events or time.monotonic() >= deadline or self._stop.is_set():
                return {"events": events, "cursor": cursor, "control": self.control(pid)}
            self._stop.wait(min(0.2, max(0, deadline - time.monotonic())))

    def _watch(self):
        while not self._stop.wait(0.2):
            try:
                self.check_expired()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Supervision check failed; Runtime watchdog remains independent')

    def check_expired(self):
        stops = []
        with self._lock:
            expired = [(pid, lease) for pid, lease in self._leases.items()
                       if time.monotonic() >= lease["deadline"]]
            for pid, lease in expired:
                state = self.hub.tasks.snapshot(lease["session_id"]).get("task") or {}
                external = state.get("scheme", {}).get("template", "").startswith("external_")
                if pid in self._active_actions or not external:
                    self._invalidate_control(pid)
                    stops.append((pid, lease))
                else:
                    # Waiting for Agent reasoning is not motion. Expire the old
                    # observation, retain the task and require a new inspected frame.
                    self._leases.pop(pid, None)
                self._expired.add(pid)
        # Each stop is independent: an unreachable A must not delay B's request.
        from concurrent.futures import ThreadPoolExecutor
        if stops:
            with ThreadPoolExecutor(max_workers=len(stops), thread_name_prefix="nerv-expiry-stop") as pool:
                list(pool.map(self._stop_expired, stops))

    def _stop_expired(self, item):
        pid, lease = item
        try:
            state = self.hub.tasks.snapshot(lease["session_id"]).get("task") or {}
            if state.get("task_id") == lease.get("task_id") and state.get("status") in {"running", "starting", "updating"}:
                self.hub.tasks.cancel(lease["session_id"], reason="External observation supervision expired")
        except Exception as error:
            try:
                self.hub.store.memory(lease["session_id"]).append("robot_activity", {
                    "source": "host", "type": "supervision_stop_unconfirmed", "ok": False,
                    "instance_id": lease["epoch"], "task_id": lease.get("task_id"),
                    "message": "External supervision expired; stop could not be confirmed: " + type(error).__name__})
            except Exception:
                pass

    def shutdown(self):
        self._stop.set()
        self._thread.join(timeout=1)
        with self._lock:
            projects = list(self._controls)
        for pid in projects:
            try:
                self.set_control(pid, accepting=False)
            except Exception:
                pass
