"""Operator Project endpoints and authenticated external-agent dispatch."""
from __future__ import annotations

import secrets
import threading
from weakref import WeakKeyDictionary

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from jsonschema.exceptions import ValidationError

from .projects import EXECUTIONS, PROJECT_TYPES, ProjectInput, ProjectService

_services = WeakKeyDictionary()
_lock = threading.RLock()


def services(hub):
    with _lock:
        if hub not in _services:
            from .agent_gateway import AgentGateway
            projects = ProjectService(hub)
            _services[hub] = (projects, AgentGateway(hub, projects))
        return _services[hub]


def shutdown(hub):
    with _lock:
        value = _services.pop(hub, None)
    if value:
        value[1].shutdown()


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    projects: list[str]
    permissions: list[str] = Field(default_factory=lambda: ["observe"])


class InvokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    arguments: dict = Field(default_factory=dict)


class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    owner_id: str | None = None


def _detail(projects, pid):
    value = projects.get(pid)
    return {"project": {k: v for k, v in value.items() if k not in ("runs", "active_run")},
            "runs": value["runs"], "active_run": value["active_run"], "readiness": projects.readiness(pid)}


def router(get_hub):
    api = APIRouter()

    def operator(request):
        # A bearer used for MCP grants no access to browser-only operator routes.
        if request.headers.get("authorization"):
            raise HTTPException(403, "Use the NERV operator interface")

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, "Project or run not found") from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error

    @api.get("/api/project-capabilities")
    def capabilities():
        return {"schema": 1, "types": PROJECT_TYPES, "executions": EXECUTIONS}

    @api.get("/api/projects")
    def projects_list():
        return services(get_hub())[0].list()

    @api.post("/api/projects")
    def project_create(request: Request, payload: ProjectInput):
        operator(request)
        return call(services(get_hub())[0].create, payload.model_dump())

    @api.get("/api/projects/{pid}")
    def project_get(pid: str):
        return call(_detail, services(get_hub())[0], pid)

    @api.patch("/api/projects/{pid}")
    def project_update(pid: str, request: Request, payload: dict):
        operator(request)
        return call(services(get_hub())[0].update, pid, payload)

    @api.post("/api/projects/{pid}/open")
    def project_open(pid: str, request: Request):
        operator(request)
        projects = services(get_hub())[0]
        call(projects.store.open, pid)
        return call(_detail, projects, pid)

    @api.post("/api/projects/{pid}/runs")
    def run_start(pid: str, request: Request):
        operator(request)
        return call(services(get_hub())[0].create_run, pid, operator=True)

    @api.post("/api/projects/{pid}/runs/{rid}/stop")
    def run_stop(pid: str, rid: str, request: Request):
        operator(request)
        return call(services(get_hub())[0].stop_run, pid, rid)

    @api.get("/api/projects/{pid}/control")
    def control_get(pid: str):
        projects, gateway = services(get_hub())
        call(projects.get, pid)
        return call(gateway.control, pid)

    @api.post("/api/projects/{pid}/control")
    def control_update(pid: str, request: Request, payload: ControlInput):
        operator(request)
        _, gateway = services(get_hub())
        if payload.action == "pause":
            return call(gateway.pause, pid)
        if payload.action in {"takeover", "release"}:
            return call(gateway.set_control, pid, accepting=False, owner_id=None)
        if payload.action == "resume":
            if not payload.owner_id:
                raise HTTPException(400, "Select the Agent receiving control")
            return call(gateway.set_control, pid, accepting=True, owner_id=payload.owner_id)
        raise HTTPException(400, "Unknown operator action")

    @api.get("/api/agent-connections")
    def connection_list():
        return services(get_hub())[1].list()

    @api.post("/api/agent-connections")
    def connection_create(request: Request, payload: ConnectionInput):
        operator(request)
        return call(services(get_hub())[1].create, **payload.model_dump())

    @api.post("/api/agent-connections/{connection_id}/revoke")
    def connection_revoke(connection_id: str, request: Request):
        operator(request)
        return call(services(get_hub())[1].revoke, connection_id)

    @api.post("/api/agent/invoke")
    def invoke(request: Request, payload: InvokeInput):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "Agent pairing is required")
        instance = getattr(request.app.state, "agent_host_instance", None)
        supplied = request.headers.get("x-nerv-host-instance", "")
        if instance and not secrets.compare_digest(instance, supplied):
            raise HTTPException(409, "Host instance changed; reconnect the MCP client")
        try:
            return services(get_hub())[1].invoke(header[7:], payload.operation, payload.arguments)
        except ValidationError as error:
            raise HTTPException(400, "Invalid Agent operation arguments") from error
        except PermissionError as error:
            raise HTTPException(403, "Agent permission denied; check pairing and project scope") from error
        except KeyError as error:
            raise HTTPException(404, "Requested resource unavailable") from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error

    return api
