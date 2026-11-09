"""Durable projects and runs over the existing task/session journal.

Projects are configuration, never a connection or motion authorization. The session
journal remains the single execution history; each run pins a project revision.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from .. import paths

PROJECT_TYPES = tuple({"id": name, "available": name in {"sim", "real"}}
                      for name in ("sim", "real", "sim2real", "real2sim", "sim2real2sim", "real2sim2real"))
EXECUTIONS = tuple({"id": name, "available": name in {"external_vlm", "external_skills", "act"}}
                   for name in ("external_vlm", "external_skills", "act", "managed", "vla", "wam", "geometric"))
ACTIVE_RUNS = frozenset({"reserved", "starting", "running", "stopping", "stop_unconfirmed"})


def _id(value):
    if not value or len(value) > 160 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
        raise ValueError("Invalid project or run ID")
    return value


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    mode: str
    robot: str = ""
    world: str = ""
    execution: str = "external_vlm"
    scheme: str = ""
    scheme_revision: int = Field(default=0, ge=0)
    sensors: list[str] = Field(default_factory=list)
    world_configuration: dict = Field(default_factory=dict)
    view_layout: dict = Field(default_factory=dict)
    notes: str = Field(default="", max_length=10000)

    def checked(self):
        if not self.name.strip():
            raise ValueError("Enter a project name")
        if self.mode not in {"sim", "real"}:
            raise ValueError("This project type is in development")
        if self.execution not in {"external_vlm", "external_skills", "act"}:
            raise ValueError("This execution capability is in development")
        if self.mode == "real" and self.world_configuration:
            raise ValueError("World configuration is simulation-only")
        # Reject non-finite values and keep presentation metadata bounded.
        if len(json.dumps(self.model_dump(), allow_nan=False)) > 128_000:
            raise ValueError("Project configuration is too large")
        return self


class ProjectStore:
    def __init__(self, root=None):
        self.root = Path(root or Path(paths.NERV_HOME) / "projects")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = FileLock(str(self.root / ".lock"))

    def get(self, project_id):
        path = self.root / _id(project_id) / "project.json"
        if not path.is_file():
            raise KeyError("Project not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self):
        projects = [self.get(p.parent.name) for p in self.root.glob("*/project.json")]
        return sorted(projects, key=lambda p: p["last_opened_at"], reverse=True)

    def create(self, payload, *, project_id=None, legacy_session=None):
        parsed = ProjectInput.model_validate(payload)
        value = (parsed if legacy_session else parsed.checked()).model_dump()
        with self.lock:
            pid = _id(project_id or "p_" + uuid.uuid4().hex)
            if (self.root / pid / "project.json").exists():
                raise ValueError("Project already exists")
            now = time.time()
            value.update(id=pid, revision=1, created_at=now, updated_at=now, last_opened_at=now)
            if legacy_session:
                value["legacy_session"] = legacy_session
            _write(self.root / pid / "revisions/1.json", value)
            _write(self.root / pid / "project.json", value)
            return value

    def update(self, project_id, payload):
        with self.lock:
            current = self.get(project_id)
            payload = dict(payload)
            expected = payload.pop("expected_revision", None)
            if type(expected) is not int or expected != current["revision"]:
                raise ValueError("Project changed; reload before saving")
            allowed = set(ProjectInput.model_fields)
            if set(payload) - allowed:
                raise ValueError("Unknown project fields")
            if payload.get("mode", current["mode"]) != current["mode"]:
                raise ValueError("Create a separate project for another environment")
            value = ProjectInput.model_validate({**{k: current[k] for k in allowed}, **payload}).checked().model_dump()
            current.update(value, revision=current["revision"] + 1, updated_at=time.time())
            _write(self.root / project_id / f"revisions/{current['revision']}.json", current)
            _write(self.root / project_id / "project.json", current)
            return current

    def open(self, project_id):
        with self.lock:
            value = self.get(project_id)
            value["last_opened_at"] = time.time()
            _write(self.root / project_id / "project.json", value)
            return value

    def runs(self, project_id):
        self.get(project_id)
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in
                       (self.root / project_id / "runs").glob("*.json")), key=lambda r: r["created_at"], reverse=True)

    def save_run(self, project_id, run):
        with self.lock:
            self.get(project_id)
            _write(self.root / _id(project_id) / "runs" / (_id(run["id"]) + ".json"), run)

    def references_session(self, session_id):
        return any(p.get("legacy_session") == session_id or
                   any(r.get("session_id") == session_id for r in self.runs(p["id"]))
                   for p in self.list())

    def migrate(self, sessions, schemes=None):
        """Verify a source backup before migration; preserve journals and pure chat."""
        with self.lock:
            marker = self.root / "migration-v1.json"
            if marker.exists():
                return json.loads(marker.read_text())
            source = Path(sessions.root)
            backup = self.root / "migration-backup-v1"
            manifest = backup / "checksums.json"
            if not manifest.exists():
                # Startup migration runs before any journal conversion or device work.
                with sessions.transaction():
                    files = sorted(p for p in source.rglob("*") if p.is_file()) if source.exists() else []
                    hashes = {}
                    for path in files:
                        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source.parent):
                            raise ValueError("Session backup cannot follow symbolic links")
                        relative = str(path.relative_to(source))
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        target = backup / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not target.exists():
                            shutil.copy2(path, target)
                        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                            raise ValueError("Session backup verification failed")
                        hashes[relative] = digest
                    _write(manifest, hashes)
            # A completed backup is the immutable migration source on every retry.
            # Never compare it with live journals, which may since have advanced.
            hashes = json.loads(manifest.read_text())
            for relative, digest in hashes.items():
                if hashlib.sha256((backup / relative).read_bytes()).hexdigest() != digest:
                    raise ValueError("Session backup verification failed")
            migrated, archived = [], []
            from .session.store import _decode_session
            ids = {Path(name).stem for name in hashes if "/" not in name and name.endswith(".json")}
            ids.update(name.split("/")[0] for name in hashes if name.endswith("/events.jsonl"))
            for sid in sorted(ids):
                events_path = backup / sid / "events.jsonl"
                events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
                if any(e.get("seq") != i + 1 for i, e in enumerate(events)):
                    raise ValueError("Invalid event sequence in migration backup")
                legacy = backup / (sid + ".json.legacy")
                if legacy.exists() and not any(e["kind"] == "migration_completed" for e in events):
                    data = json.loads(legacy.read_text())
                elif events:
                    metadata = next((e["data"] for e in reversed(events) if e["kind"] == "metadata"), None)
                    if metadata is None:
                        raise ValueError("Session backup has no metadata")
                    data = {**metadata, "messages": [e["data"] for e in events if e["kind"] == "message"]}
                else:
                    data = json.loads((backup / (sid + ".json")).read_text())
                session = _decode_session(data)
                if getattr(session, "project_id", ""):
                    continue
                if not session.body:
                    archived.append(session.id)
                    continue
                pid = "p_legacy_" + _id(session.id)
                if not (self.root / pid / "project.json").exists():
                    execution = "managed" if session.task_model else "external_vlm"
                    revision = session.control_scheme_revision
                    if session.control_scheme and schemes:
                        try:
                            scheme = schemes.get(session.control_scheme, revision or None)
                            execution = {"fixed_policy": "act", "external_vlm": "external_vlm",
                                         "external_skills": "external_skills"}.get(scheme.template, "managed")
                            revision = scheme.revision
                        except (ValueError, FileNotFoundError):
                            pass  # Keep the missing reference visible for repair.
                    self.create({"name": (session.title.strip() or "Imported project")[:120], "mode": session.mode,
                                 "robot": session.body, "world": session.world or "",
                                 "scheme": session.control_scheme, "scheme_revision": revision,
                                 "execution": execution, "sensors": session.sensors},
                                project_id=pid, legacy_session=session.id)
                rid = "r_legacy_" + session.id
                if not (self.root / pid / "runs" / (rid + ".json")).exists():
                    self.save_run(pid, {"id": rid, "project_id": pid, "project_revision": 1,
                                       "session_id": session.id, "status": "interrupted", "created_at": self.get(pid)["created_at"],
                                       "ended_at": time.time(), "reason": "Imported history; no actions resumed",
                                       "snapshot": self.get(pid)})
                migrated.append(pid)
            result = {"schema": 1, "projects": migrated, "archived_sessions": archived,
                      "backup": str(backup), "completed_at": time.time()}
            _write(marker, result)
            return result


class ProjectService:
    def __init__(self, hub, root=None):
        self.hub = hub
        self.store = ProjectStore(root) if root else getattr(hub, "project_store", None) or ProjectStore()
        self._lock = threading.RLock()
        # Recover durable run metadata without invoking any device or task API.
        for project in self.store.list():
            for run in self.store.runs(project["id"]):
                if run["status"] in ACTIVE_RUNS:
                    run.update(status="interrupted", ended_at=time.time(), reason="Host restarted; reconnect explicitly")
                    self.store.save_run(project["id"], run)

        from ..brain_core.worlds import WorldInstances
        self.worlds = WorldInstances(self)

    def _pin_scheme(self, value):
        value = dict(value)
        if value.get("scheme"):
            scheme = self.hub.schemes.get(value["scheme"], value.get("scheme_revision") or None)
            if scheme.robot != value.get("robot") or scheme.mode != value.get("mode"):
                raise ValueError("Control scheme does not match this project")
            expected = {"external_vlm": "external_vlm", "external_skills": "external_skills", "act": "fixed_policy"}.get(value.get("execution", "external_vlm"))
            if expected and scheme.template != expected:
                raise ValueError("Control scheme does not match the execution structure")
            value["scheme_revision"] = scheme.revision
        else:
            value["scheme_revision"] = 0
        return value

    def create(self, payload):
        return self.store.create(self._pin_scheme(payload))

    def update(self, project_id, payload):
        with self._lock, self.store.lock:
            current = self.store.get(project_id)
            values = dict(payload)
            if {"scheme", "scheme_revision", "robot", "mode", "execution"} & values.keys():
                if "scheme" in values and "scheme_revision" not in values:
                    values["scheme_revision"] = 0
                pinned = self._pin_scheme({**current, **values})
                values["scheme_revision"] = pinned["scheme_revision"]
            return self.store.update(project_id, values)

    def active_run(self, project_id):
        return next((r for r in self.store.runs(project_id) if r["status"] in ACTIVE_RUNS), None)

    def get(self, project_id):
        value = self.store.get(project_id)
        return {**value, "runs": self.store.runs(project_id), "active_run": self.active_run(project_id)}

    def list(self):
        return [{**p, "active_run": self.active_run(p["id"])} for p in self.store.list()]

    def readiness(self, project_id):
        project = self.store.get(project_id)
        issues = []
        if project["execution"] == "managed":
            issues.append({"code": "legacy_internal_model", "reason": "Choose an external control scheme", "destination": "projects"})
        if project["robot"] not in self.hub.registry.bodies:
            issues.append({"code": "robot_missing", "destination": "robots"})
        if project["world"] in self.hub.registry.worlds and self.hub.registry.world(project["world"]).kind != project["mode"]:
            issues.append({"code": "world_mode_mismatch", "destination": "worlds"})
        if project["mode"] == "sim":
            if project["world"] not in self.hub.registry.worlds:
                issues.append({"code": "world_missing", "destination": "worlds"})
            elif project["robot"] in self.hub.registry.bodies and self.hub.registry.world(project["world"]).kind == "sim":
                from .registry.compat import check
                ok, reason = check(self.hub.registry.world(project["world"]), self.hub.registry.body(project["robot"]))
                if not ok:
                    issues.append({"code": "incompatible_world", "reason": reason, "destination": "worlds"})
        return {"ready": not issues, "issues": issues}

    def require_session(self, project_id):
        run = self.active_run(project_id)
        if not run or run["status"] != "running" or not run.get("session_id"):
            raise ValueError("Start the project before requesting observations or actions")
        session = self.hub.store.get(run["session_id"])
        if self.hub._control_error(session):
            raise ValueError("Runtime changed; end this run and reconnect")
        return session

    def create_run(self, project_id, *, operator=False):
        with self._lock:
            project = self.store.get(project_id)
            current = self.active_run(project_id)
            if current and current["status"] != "reserved":
                if current["status"] != "running":
                    raise ValueError("Resolve the current run before starting another")
                session = self.require_session(project_id)
                return {"run": current, "session": session.summary()}
            if project["mode"] == "real" and not operator:
                raise ValueError("A person must connect the real robot in NERV")
            if not self.worlds.for_run(current) and any(self.active_run(p["id"]) for p in self.store.list() if p["id"] != project_id):
                raise ValueError("End the active world before starting another project")
            if current and current["project_revision"] != project["revision"]:
                raise ValueError("Project changed after Brain allocation; end the reserved Run")
            ready = self.readiness(project_id)
            if not ready["ready"]:
                raise ValueError("Project needs configuration: " + ", ".join(i["code"] for i in ready["issues"]))
            if project["scheme"]:
                scheme = self.hub.schemes.get(project["scheme"], project["scheme_revision"] or None)
                if scheme.robot != project["robot"] or scheme.mode != project["mode"]:
                    raise ValueError("Control scheme does not match this project")
                expected = {"external_vlm": "external_vlm", "external_skills": "external_skills", "act": "fixed_policy"}.get(project["execution"])
                if expected and scheme.template != expected:
                    raise ValueError("Control scheme does not match the execution structure")
            run = current or {"id": "r_" + uuid.uuid4().hex, "project_id": project_id,
                   "project_revision": project["revision"], "snapshot": project,
                   "status": "starting", "session_id": "", "created_at": time.time()}
            run["status"] = "starting"
            self.store.save_run(project_id, run)
            try:
                session = self.worlds.attach(run) if self.worlds.for_run(run) else self.hub.new_session("operator", project["robot"], project["world"] or None,
                    project["sensors"], [], project["mode"], project["world_configuration"], operator_workspace=True,
                    project_id=project_id, run_id=run["id"], project_revision=project["revision"])
                bound_session = self.hub.store.get(session["id"])
                for key in ("brain_instance_id", "robot_instance_id", "world_instance_id"):
                    setattr(bound_session, key, run.get(key, ""))
                bound_session.robot_profile = project["robot"]
                bound_session.binding_generation = run.get("binding_generation", 0)
                bound_session.world_generation = run.get("world_generation", 0)
                self.hub.store.save(bound_session)
                handle = self.hub.launcher.nodes.get('body:' + bound_session.body)
                run.update(session_id=session["id"], status="running", world_epoch=bound_session.epoch,
                           robot_epoch=handle.meta.get('epoch', '') if handle else '')
                bound_session.robot_epoch = run["robot_epoch"]
                self.hub.store.save(bound_session)
                self.store.save_run(project_id, run)
                if project["scheme"]:
                    self.hub.tasks.select_scheme(session["id"], project["scheme"], scheme.revision)
                self.store.open(project_id)
                return {"run": run, "session": self.hub.store.get(session["id"]).summary()}
            except Exception as error:
                # If an instance exists it must remain visible, even if scheme setup failed.
                run.update(status="running" if run["session_id"] else "failed", reason=str(error))
                self.store.save_run(project_id, run)
                raise

    def select_scheme(self, session_id, scheme_id, revision=None):
        with self._lock, self.store.lock:
            session = self.hub.store.get(session_id)
            project = self.store.get(session.project_id)
            if self.require_session(project["id"]).id != session_id:
                raise ValueError("This run is no longer active")
            scheme = self.hub.schemes.get(scheme_id, revision)
            result = self.hub.tasks.select_scheme(session_id, scheme_id, scheme.revision)
            execution = {"fixed_policy": "act", "external_vlm": "external_vlm",
                         "external_skills": "external_skills"}.get(scheme.template, "managed")
            self.store.update(project["id"], {"expected_revision": project["revision"],
                "scheme": scheme_id, "scheme_revision": scheme.revision, "execution": execution})
            return result

    def rebuild(self, project_id, values):
        with self._lock:
            project = self.store.get(project_id)
            if project["mode"] != "sim":
                raise ValueError("Only simulation projects can rebuild a world")
            self.end_run(project_id)
            self.store.update(project_id, {"expected_revision": project["revision"], "world_configuration": values})
            return self.create_run(project_id, operator=True)

    def stop_run(self, project_id, run_id):
        with self._lock:
            run = self.active_run(project_id)
            if not run or run["id"] != run_id:
                raise ValueError("This run is no longer active")
            if run["status"] == "reserved":
                run.update(status="ended", ended_at=time.time(), reason="Reservation cancelled")
                self.store.save_run(project_id, run)
                return {"ok": True, "run": run}
            session = self.hub.store.get(run["session_id"])
            run.update(status="stopping")
            self.store.save_run(project_id, run)
            try:
                result = self.hub.tasks.cancel(session.id, reason="Project run ended")
                if not result.get("ok"):
                    raise ValueError("Stop has not been confirmed")
                if run.get("shared_world"):
                    disarmed = self.hub.arm(session.id, False)
                    if not disarmed.get('ok'):
                        raise ValueError('Shared robot disarm was not confirmed')
                    self.hub.store.set_status(session.id, "frozen")
                    # Runtime remains in supervised hold; shared physics stays alive.
                elif session.mode == "sim":
                    # Session epoch identifies the world; stop confirmation uses
                    # the producer's distinct instance identity.
                    handle = self.hub.launcher.nodes.get("body:" + session.body)
                    if not handle:
                        raise ValueError("Simulation producer is missing; stop is unconfirmed")
                    self.hub.stop_simulation("body:" + session.body, session.world,
                                             handle.meta.get("epoch", ""), session.id)
                else:
                    self.hub.arm(session.id, False)
                    client = self.hub.body_client(session.body)
                    closed = client.post("/device/close", {}) if client else {"ok": False}
                    if not closed.get("ok"):
                        raise ValueError("Device disconnect has not been confirmed")
                    self.hub.launcher.stop("body:" + session.body)
                    self.hub._bodies.pop(session.body, None)
                    self.hub._bound_runtime = None
                    # Disconnect uses the adapter's torque convention, never torque-off.
                    self.hub.store.set_status(session.id, "frozen")
                run.update(status="ended", ended_at=time.time(), reason="Run ended by operator")
                self.store.save_run(project_id, run)
                return {"ok": True, "run": run}
            except Exception as error:
                run.update(status="stop_unconfirmed", reason=str(error))
                self.store.save_run(project_id, run)
                raise

    def end_run(self, project_id):
        run = self.active_run(project_id)
        if run is None:
            raise ValueError("No active run")
        return self.stop_run(project_id, run["id"])

    def stop_simulation(self, key, world, epoch, session_id=None):
        """Keep the legacy operator control on the same Project lifecycle."""
        with self._lock:
            for project in self.store.list():
                run = self.active_run(project["id"])
                if not run or not run.get("session_id"):
                    continue
                session = self.hub.store.get(run["session_id"])
                if key != "body:" + session.body:
                    continue
                handle = self.hub.launcher.nodes.get(key)
                if (not handle or not epoch or handle.meta.get("epoch") != epoch
                        or session.world != world or session.mode != "sim"
                        or (session_id and session_id != session.id)):
                    raise ValueError("The simulation changed; refresh before stopping it")
                return self.stop_run(project["id"], run["id"])
            return self.hub.stop_simulation(key, world, epoch, session_id)
