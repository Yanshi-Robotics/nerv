"""NERV/Operator over HTTP + SSE. The web app is a client of this and nothing else."""
from __future__ import annotations

import base64
import json
import os
import threading
from typing import Optional

import httpx

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, config, paths
from ..brain.providers import list_brains
from ..vlm.store import ModelRegistry, VisualModel
from . import messages, trust
from . import explore
from .hub import Nerv
from .session import log as slog

nerv = Nerv()
_shutdown_requested = threading.Event()
app = FastAPI(title="NERV", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def operator_origin(request: Request, call_next):
    if _shutdown_requested.is_set():
        return JSONResponse(status_code=503, content={"detail": "NERV is shutting down"})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        from urllib.parse import urlsplit
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).hostname not in ("localhost", "127.0.0.1", "::1") or
                       (urlsplit(origin).netloc != request.headers.get("host") and origin not in config.CORS_ORIGINS)):
            return JSONResponse(status_code=403, content={"detail": "Operator action requires the local NERV interface"})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def private_validation_error(_request, error):
    # Never reflect a rejected credential or request body into a 422 response.
    return JSONResponse(status_code=422, content={"detail": [
        {key: value for key, value in item.items() if key in ("loc", "msg", "type")}
        for item in error.errors()]})


def request_shutdown():
    """End observers and invalidate model decisions before HTTP request draining."""
    _shutdown_requested.set()
    from .setup_checks import checks
    checks.cancel()
    nerv.request_shutdown()
    from .module_jobs import shutdown as stop_installers
    stop_installers()


@app.on_event("shutdown")
def _shutdown() -> None:
    from .setup_preview import preview
    preview.close()
    from .setup_checks import checks
    checks.close()
    nerv.shutdown()


class NewSessionIn(BaseModel):
    brain: Optional[str] = None
    body: Optional[str] = None
    world: Optional[str] = None
    sensors: list[str] = []
    agent_tools: Optional[list[str]] = None
    mode: str = "sim"
    world_configuration: dict = {}


class ChatIn(BaseModel):
    session: str
    text: str


class TaskOperationIn(BaseModel):
    operation: str
    arguments: dict = {}


class TaskModelIn(BaseModel):
    model: str
    effort: str | None = None


class ArmIn(BaseModel):
    armed: bool


class TeleopIn(BaseModel):
    name: str
    arguments: dict = {}


class BrainIn(BaseModel):
    brain: str


class ConfigIn(BaseModel):
    key: str
    value: str


class StopSimulationIn(BaseModel):
    expected_world: str
    expected_epoch: str
    session_id: Optional[str] = None


def _default_brain() -> str:
    return ModelRegistry().selection() or "deepseek-flash"


def _models_idle():
    if nerv._runs or any((c.post("/executions/status") or {}).get("running") for c in nerv._bodies.values()):
        raise HTTPException(409, "Finish the active task before changing model configuration")


@app.get("/api/runtime/installations")
def runtime_installations():
    from .runtime_management import installations
    return installations()


@app.get('/api/system')
def system_profile():
    from .module_jobs import system_description
    return system_description()


@app.post('/api/system/detect')
def detect_system():
    from .module_jobs import system_description
    return system_description(True)


@app.put('/api/system/settings')
def save_system_settings(payload: dict):
    from .module_jobs import catalog, system_description, system_facts
    from .. import installation, install_system
    if nerv._bound_runtime or any(j['state'] in ('queued', 'running') for j in catalog()['jobs']):
        raise HTTPException(409, 'Close the robot instance and pause installation before changing system settings')
    try:
        install_system.save_settings(installation.home(), payload, system_facts())
        return system_description()
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@app.post('/api/system/settings/reset')
def reset_system_settings():
    from ..install_system import DEFAULTS
    return save_system_settings(DEFAULTS)


@app.get('/api/modules/{module}/plan')
def module_plan(module: str):
    from .. import installation, install_plan
    from .module_jobs import system_facts
    manifest, _ = installation.read_manifest()
    if module not in manifest['products']:
        raise HTTPException(404, 'Unknown module')
    return install_plan.describe(installation.home(), manifest, module, system_facts())


@app.post('/api/modules/{job_id}/pause')
def module_pause(job_id: str):
    from .module_jobs import pause
    try:
        return pause(job_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@app.post('/api/modules/{job_id}/resume')
def module_resume(job_id: str):
    from .module_jobs import resume
    if nerv._bound_runtime:
        raise HTTPException(409, 'Close the robot instance before continuing installation')
    try:
        return resume(job_id)
    except (ValueError, OSError, TimeoutError) as error:
        raise HTTPException(409, str(error)) from None


@app.get("/api/modules")
def modules_catalog():
    from .module_jobs import catalog
    from .registry import Registry
    if not nerv._bound_runtime:
        nerv.registry = Registry()
        nerv.launcher.registry = nerv.registry
    try:
        return catalog()
    except (ValueError, OSError):
        raise HTTPException(503, "Component catalogue unavailable. Restore the release bundle "
                            "for this NERV installation, then retry.") from None


@app.get("/api/modules/jobs/{job_id}/log")
def module_log(job_id: str):
    from .module_jobs import log_tail
    try:
        return log_tail(job_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except FileNotFoundError:
        raise HTTPException(404, "Installation log not found") from None


@app.get("/api/releases/check")
def check_release():
    from ..installation import update
    try:
        return update(check=True)
    except Exception as error:
        raise HTTPException(503, str(error))


@app.post("/api/modules/{module}/install")
def module_install(module: str):
    if nerv._bound_runtime:
        raise HTTPException(409, "Close the robot instance before installing components")
    from .module_jobs import start
    try:
        return start(module)
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error))


@app.post("/api/modules/{job_id}/cancel")
def module_cancel(job_id: str):
    from .module_jobs import cancel
    try:
        return cancel(job_id)
    except ValueError as error:
        raise HTTPException(400, str(error))


class RuntimeManagementIn(BaseModel):
    python: str
    provider: str
    operation: str
    payload: dict = {}


@app.post("/api/runtime/manage")
def manage_runtime(body: RuntimeManagementIn, request: Request):
    from urllib.parse import urlsplit
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host") and origin not in config.CORS_ORIGINS:
        raise HTTPException(403, "runtime management requires an allowed local origin")
    from .runtime_management import manage
    from .registry import Registry
    with nerv._node_lock:
        from .setup_checks import checks
        if checks.active():
            raise HTTPException(409, "Wait for or cancel the device check")
        if body.operation in ("configure", "configure_standard", "import") and nerv._bound_runtime:
            raise HTTPException(409, "Close the current robot instance before changing its configuration")
        try:
            result = manage(body.python, body.provider, body.operation, body.payload)
            if body.operation in ("configure", "import"):
                nerv.registry = Registry()
                nerv.launcher.registry = nerv.registry
            return result
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error))


def _setup_installation(provider):
    from .runtime_management import installations
    value = next((item for item in installations() if item["provider"] == provider), None)
    if not value:
        raise HTTPException(409, "Install the device runtime first")
    return value


@app.post("/api/operator/directories")
def operator_directories(payload: dict):
    from .local_files import directories
    try:
        return directories(payload.get("path"), int(payload.get("offset", 0)))
    except (OSError, ValueError) as error:
        raise HTTPException(400, str(error))


@app.get("/api/setup-health/{product}")
def setup_health(product: str, refresh: bool = False):
    from .computer_check import report
    return report(product, refresh)


@app.post("/api/setup-checks/{provider}/start")
def setup_check_start(provider: str, payload: dict):
    from .setup_checks import checks
    from .setup_preview import preview
    with nerv._node_lock:
        if nerv._bound_runtime or preview.process is not None:
            raise HTTPException(409, "Close the robot instance and camera preview before checking")
        try:
            return checks.start(_setup_installation(provider), payload)
        except (ValueError, OSError) as error:
            raise HTTPException(409, str(error))


@app.post("/api/setup-checks/jobs/{identifier}/{operation}")
def setup_check_job(identifier: str, operation: str):
    from .setup_checks import checks
    try:
        if operation == "cancel":
            return checks.cancel(identifier)
        if operation == "status":
            return checks.get(identifier, renew=True)
        raise HTTPException(404, "Unknown check operation")
    except (ValueError, OSError) as error:
        raise HTTPException(404, str(error))


@app.post("/api/setup/{provider}/{operation}")
def adapter_setup(provider: str, operation: str, payload: dict):
    installation = _setup_installation(provider)
    if operation == "acceptance":
        if payload.get("accepted") is not True:
            raise HTTPException(400, "Explicit supervised acceptance is required")
        body = nerv.registry.bodies.get(payload.get("robot"))
        if not body or "real" not in body.buses or body.buses["real"].provider != provider:
            raise HTTPException(400, "Select a device registered by this provider")
        nerv.launcher.supervised_acceptance = {(body.name, "real")}
        return {"ok": True, "verified": False}
    if operation not in ("environment", "prerequisite_status", "scheme_options", "discover", "calibrations", "validate_calibration", "configure_standard", "policies", "import", "inspect", "check"):
        raise HTTPException(404, "Unknown setup operation")
    from .runtime_management import manage
    with nerv._node_lock:
        from .setup_checks import checks
        if checks.active() and operation != "prerequisite_status":
            raise HTTPException(409, "Wait for or cancel the device check")
        if operation in ("configure_standard", "import") and nerv._bound_runtime:
            raise HTTPException(409, "Close the robot instance before changing its configuration")
        try:
            result = manage(installation["python"], installation["provider"], operation, payload)
            if operation in ("configure_standard", "import"):
                from .registry import Registry
                nerv.registry = Registry()
                nerv.launcher.registry = nerv.registry
            return result
        except Exception as error:
            raise HTTPException(400, str(error))


@app.post("/api/setup-camera/{provider}/{operation}")
def setup_camera(provider: str, operation: str, payload: dict):
    from .setup_preview import preview
    with nerv._node_lock:
        if operation == "close":
            return preview.close()
        from .setup_checks import checks
        if nerv._bound_runtime or checks.active():
            raise HTTPException(409, "Close the robot instance or device check before opening setup preview")
        if operation not in ("open", "frames"):
            raise HTTPException(404, "Unknown preview operation")
        try:
            from .runtime_management import manage
            installation = _setup_installation(provider)
            descriptor = manage(installation["python"], provider, "describe", {})
            module = descriptor.get("camera_preview_module")
            if not module:
                raise ValueError("This adapter does not provide camera setup")
            return preview.request(installation["python"], operation, payload.get("cameras"), module=module)
        except Exception as error:
            raise HTTPException(400, str(error))


@app.get("/api/vlm")
def vlm_models():
    registry = ModelRegistry()
    binding = nerv.binding_signature()
    return {"models": registry.list(binding), "providers": registry.providers(),
            "credential_path": str(registry.credentials.path),
            "binding_signature": binding,
            "selection": registry.selection(), "task_locked": bool(nerv._runs)}


@app.put("/api/vlm/{model_id}")
def vlm_save(model_id: str, model: VisualModel):
    _models_idle()
    if model.id != model_id:
        raise HTTPException(400, "model id does not match the URL")
    ModelRegistry().upsert(model.model_dump())
    nerv._brains.clear()
    return {"ok": True}


class CredentialIn(BaseModel):
    value: str | None = None


@app.put("/api/credentials/{reference}")
def credential_save(reference: str, payload: CredentialIn):
    _models_idle()
    if payload.value is not None:
        raise HTTPException(400, "Use the model configuration test to register a key")
    registry = ModelRegistry()
    try:
        registry.credentials.set(reference, payload.value)
        registry.invalidate_credential(reference)
        nerv._brains.clear()
        return {"ok": True, **registry.credentials.describe(reference)}
    except (ValueError, PermissionError) as error:
        raise HTTPException(400, str(error))


@app.post("/api/vlm/{model_id}/configure")
async def vlm_configure(model_id: str, payload: CredentialIn, request: Request):
    import asyncio
    import threading
    from ..vlm.errors import ModelError
    _models_idle()
    cancelled = threading.Event()
    async def watch_disconnect():
        while not cancelled.is_set():
            if await request.is_disconnected():
                cancelled.set()
                return
            await asyncio.sleep(0.1)
    watcher = asyncio.create_task(watch_disconnect())
    try:
        result = await asyncio.to_thread(ModelRegistry().configure, model_id, payload.value,
                                         cancelled=cancelled.is_set)
        if result["saved"]:
            nerv._brains.clear()
        return result
    except ModelError as error:
        raise HTTPException(400, error.public()) from None
    except (ValueError, KeyError):
        raise HTTPException(400, "Select a registered visual model") from None
    finally:
        cancelled.set()
        watcher.cancel()


class SelectionIn(BaseModel):
    effort: str | None = None


@app.post("/api/vlm/{model_id}/select")
def vlm_select(model_id: str, payload: SelectionIn):
    _models_idle()
    try:
        ModelRegistry().select(model_id, payload.effort)
        nerv._brains.clear()
        return {"ok": True}
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error))


class PhysicalAcceptanceIn(BaseModel):
    binding_signature: str
    signature: str
    status: str
    evidence: str


@app.post("/api/vlm/{model_id}/acceptance")
def vlm_acceptance(model_id: str, payload: PhysicalAcceptanceIn):
    _models_idle()
    try:
        if not payload.binding_signature or payload.binding_signature != nerv.binding_signature():
            raise ValueError("physical evidence does not match the current prepared robot binding")
        ModelRegistry().record_acceptance(model_id, payload.signature, payload.status, payload.evidence,
                                          payload.binding_signature)
        return {"ok": True}
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error))


@app.post("/api/vlm/{model_id}/validate")
def vlm_validate(model_id: str):
    _models_idle()
    try:
        return ModelRegistry().validate(model_id)
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error))


@app.post("/api/sessions/{sid}/runtime/{operation:path}")
def runtime_operation(sid: str, operation: str, payload: dict):
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    session = nerv.store.get(sid)
    if nerv._control_error(session) or not session.body:
        raise HTTPException(409, "this session does not control the robot")
    task = nerv.tasks.snapshot(sid)["task"] or {}
    active = task.get("status") in ("starting", "running", "updating", "stopping")
    if operation == "operator/supervise":
        if active and task.get("scheme", {}).get("template") != "fixed_policy":
            raise HTTPException(403, "Decision tasks use their own supervision")
    elif active or sid in nerv._runs:
        raise HTTPException(409, "Stop the task before preparing or loading")
    if operation not in ("prepare", "policies/load", "policies/check", "device/close", "device/torque-off", "operator/supervise"):
        raise HTTPException(404, "unknown runtime operation")
    client = nerv.body_client(session.body)
    if client is None:
        raise HTTPException(503, "body node offline")
    if operation in ("device/close", "device/torque-off"):
        nerv.arm(sid, False)
    if operation == "prepare":
        from .setup_checks import checks
        if checks.active():
            raise HTTPException(409, "Wait for or cancel the device check")
        from .setup_preview import preview
        preview.close()
    result = client.post("/" + operation, payload)
    client.refresh()
    if operation in ("device/close", "device/torque-off") and result.get("ok") and session.mode == "real":
        nerv.launcher.stop(f"body:{session.body}")
        nerv._bodies.pop(session.body, None)
        nerv._bound_runtime = None
        nerv.store.set_status(sid, "reconnect_required")
    return result


@app.get("/api/sessions/{sid}/runtime")
def runtime_status(sid: str):
    session = nerv.store.get(sid)
    client = nerv.body_client(session.body) if session.body else None
    status = client.status() if client else None
    if not status:
        raise HTTPException(503, "body node offline")
    return status


# ---- registry / brains ---------------------------------------------------------------------
@app.get("/api/registry")
def registry() -> dict:
    out = nerv.registry.summary()
    out["brains"] = list_brains()
    out["default_brain"] = _default_brain()
    return out


@app.get("/api/brains")
def brains() -> list:
    return list_brains()


@app.get("/api/worlds/{world}/explore")
def world_explore(world: str) -> dict:
    try:
        _directory, manifest = explore.read_manifest(nerv.registry.world(world))
        return manifest
    except KeyError:
        raise HTTPException(404, "Unknown world")
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.get("/api/worlds/{world}/explore/assets/{filename:path}")
def world_explore_asset(world: str, filename: str):
    try:
        path = explore.asset_path(nerv.registry.world(world), filename)
        return FileResponse(path, headers={"Cache-Control": "no-cache"})
    except (KeyError, FileNotFoundError) as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.get("/api/check")
def check(brain: str = Query(...)) -> dict:
    try:
        b = nerv.brain(brain)
    except KeyError:
        return {"ok": False, "message": messages.UNKNOWN_BRAIN_REPLY}
    spec = {x["name"]: x for x in list_brains()}.get(brain, {})
    if not spec.get("available", False):
        return {"ok": False, "message": messages.BRAIN_NOT_CONFIGURED_REPLY}
    return {"ok": True, "model": b.model, "vision": b.vision}


# ---- nodes (control plane) ----------------------------------------------------------------
@app.get("/api/nodes")
def nodes() -> list:
    out = []
    for h in nerv.launcher.list():
        c = nerv.node_client(h["key"])
        item = dict(h)
        world = nerv.registry.worlds.get(h["meta"].get("world", ""))
        item["local_simulation"] = bool(h["kind"] == "body" and not h["attached"] and world and world.kind == "sim")
        if c is not None:
            item["online"] = c.online()
            try:
                d = c.trust_decision()
                item["trust"] = {"state": d.state, "reason": d.reason, "changes": d.changes}
                caps = c.capabilities()
                item["robot_actions"] = [t.name for t in caps.robot_actions]
                item["agent_tools"] = [t.name for t in caps.agent_tools]
                item["family"] = caps.family
                item["sensors"] = caps.sensors
            except Exception as e:
                item["trust"] = {"state": "offline", "reason": str(e)}
        else:
            wc_key = h["key"].split(":", 1)[1]
            if h["kind"] == "world" and "/" in wc_key:
                w, b = wc_key.split("/", 1)
                wc = nerv.world_client(w, b)
                item["online"] = wc.online() if wc else False
                item["sensors"] = wc.sensors() if wc else []
        out.append(item)
    return out


@app.get("/api/nodes/{key:path}/manifest")
def node_manifest(key: str) -> dict:
    c = nerv.node_client(key)
    if c is None:
        raise HTTPException(404, "no such node")
    raw = c.raw_capabilities()
    d = c.trust_decision()
    return {"key": key, "domain": "robot_actions" if key.startswith("body:") else "agent_tools", "url": c.base, "manifest": trust.manifest(c.base, raw.operations, raw.guidance),
            "trust": {"state": d.state, "reason": d.reason, "changes": d.changes},
            "hash": trust.manifest_hash(trust.manifest(c.base, raw.operations, raw.guidance)),
            "family": raw.family, "sensors": raw.sensors}


class AccessApprovalIn(BaseModel):
    expected_hash: str


@app.post("/api/nodes/{key:path}/approve")
def node_approve(key: str, payload: AccessApprovalIn) -> dict:
    c = nerv.node_client(key)
    if c is None:
        raise HTTPException(404, "no such node")
    try:
        h = c.approve(expected_hash=payload.expected_hash)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"ok": True, "hash": h}


@app.post("/api/nodes/{key:path}/refresh")
def node_refresh(key: str) -> dict:
    c = nerv.node_client(key)
    if c is None:
        raise HTTPException(404, "no such node")
    c.refresh()
    return {"ok": True}


@app.post("/api/nodes/{key:path}/stop-simulation")
def stop_simulation(key: str, inp: StopSimulationIn) -> dict:
    try:
        return nerv.stop_simulation(key, inp.expected_world, inp.expected_epoch, inp.session_id)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, e.args[0] if e.args else str(e))


@app.post("/api/nodes/{key:path}/config")
def node_config(key: str, inp: ConfigIn) -> dict:
    c = nerv.node_client(key)
    if c is None:
        raise HTTPException(404, "no such node")
    if key.startswith("body:"):
        body = key.partition(":")[2]
        # Match the body protocol's accepted enable values, including whitespace.
        enabling = inp.key == "armed" and inp.value.strip().lower() in ("1", "true", "yes", "on", "armed")
        with nerv.body_control_lock(body):
            # Recheck after waiting: scene entry may have acquired control meanwhile.
            if enabling and nerv.scene_tests.blocks(body):
                raise HTTPException(409, "End scene testing before arming the body")
            r = c.set_config(inp.key, inp.value)
            c.refresh()
            return r
    r = c.set_config(inp.key, inp.value)
    c.refresh()
    return r


@app.get("/api/nodes/{key:path}/status")
def node_status(key: str) -> dict:
    c = nerv.node_client(key)
    if c is not None:
        return c.status() or {}
    kind, _, rest = key.partition(":")
    if kind == "world" and "/" in rest:
        w, b = rest.split("/", 1)
        wc = nerv.world_client(w, b)
        return (wc.status() if wc else None) or {}
    raise HTTPException(404, "no such node")


# ---- sessions -------------------------------------------------------------------------------
@app.post("/api/sessions")
def new_session(inp: NewSessionIn) -> dict:
    try:
        from .session import require_scene
        require_scene(inp.body, inp.world, inp.mode)
        if not inp.brain or inp.brain == "operator" or inp.brain not in {b["name"] for b in list_brains()}:
            raise ValueError("select a registered visual model")
        return nerv.new_session(inp.brain or _default_brain(), inp.body, inp.world, inp.sensors, inp.agent_tools, inp.mode, inp.world_configuration)
    except (ValueError, KeyError, RuntimeError, TimeoutError, FileNotFoundError) as e:
        from ..vlm.errors import ModelError
        raise HTTPException(400, e.public() if isinstance(e, ModelError) else e.args[0] if e.args else str(e))


@app.get("/api/sessions")
def list_sessions() -> list:
    return nerv.store.list()


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    s = nerv.store.get(sid)
    return {**s.summary(), "messages": s.messages}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str) -> dict:
    nerv.stop(sid) if nerv.store.exists(sid) else None
    if sid in nerv._runs:
        raise HTTPException(409, "Wait for the interrupted model request to exit before deleting its session")
    return {"ok": nerv.store.delete(sid)}


@app.post("/api/sessions/{sid}/interrupt")
def interrupt_session(sid: str) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    nerv.stop_reply(sid)
    return {"ok": True}


@app.get("/api/sessions/{sid}/world-control")
def world_control_descriptor(sid: str):
    try:
        return nerv.world_controls.capabilities(sid)
    except httpx.HTTPError as error:
        raise HTTPException(503, "World node unavailable. End this instance and create a new one.") from error
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/sessions/{sid}/world-control")
def world_control_invoke(sid: str, payload: dict):
    try:
        if "instance_id" not in payload or "revision" not in payload:
            raise ValueError("Instance and revision are required")
        return nerv.world_controls.invoke(sid, payload["command"], payload.get("arguments", {}),
            instance_id=payload["instance_id"], revision=payload["revision"])
    except Exception as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/control-schemes")
def control_schemes(sid: str | None = None):
    from .control_schemes import resolve
    session = nerv.store.get(sid) if sid else None
    items = []
    for scheme in nerv.schemes.list():
        item = {**scheme.model_dump(), "structure": scheme.structure(), "ready": False}
        try:
            if session:
                resolve(nerv, session, scheme)
                item["ready"] = True
        except (ValueError, OSError) as error:
            item["reason"] = str(error)
        items.append(item)
    actions = []
    if session and session.body and nerv.body_client(session.body):
        actions = [{"name": a.name, "description": a.description, "kind": a.kind}
                   for a in nerv.body_client(session.body).capabilities().operations if a.kind in ("skill", "primitive")]
    selected = None
    if session and session.control_scheme:
        pinned = nerv.schemes.get(session.control_scheme, session.control_scheme_revision)
        selected = {**pinned.model_dump(), "ready": False}
        try:
            resolve(nerv, session, pinned)
            selected["ready"] = True
        except (ValueError, OSError) as error:
            selected["reason"] = str(error)
    return {"schemes": items, "actions": actions, "selected": selected}


@app.post("/api/control-schemes")
def save_control_scheme(payload: dict):
    try:
        return nerv.schemes.save(payload).model_dump()
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error))


@app.post("/api/sessions/{sid}/control-scheme")
def select_control_scheme(sid: str, payload: dict):
    try:
        return nerv.tasks.select_scheme(sid, payload["id"], payload.get("revision"))
    except (KeyError, ValueError, OSError) as error:
        raise HTTPException(409, str(error))


@app.get("/api/sessions/{sid}/robot-tasks")
def robot_tasks(sid: str, after: int = Query(0, ge=0)):
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.tasks.snapshot(sid, after)


@app.post("/api/sessions/{sid}/robot-tasks")
def robot_task_operation(sid: str, inp: TaskOperationIn):
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    try:
        from jsonschema import validate
        schema = next((schema for name, _, schema in nerv.tasks.capabilities() if name == inp.operation), None)
        if schema is None:
            raise ValueError("Unknown task operation")
        validate(inp.arguments, schema)
        return nerv.tasks.invoke(sid, inp.operation, inp.arguments)
    except Exception as error:
        raise HTTPException(409, str(error))


@app.get("/api/sessions/{sid}/robot-events")
def robot_events(sid: str, request: Request, after: int = Query(0, ge=0)):
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")

    async def events():
        import asyncio
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = after
        while not _shutdown_requested.is_set() and nerv.store.exists(sid) and not await request.is_disconnected():
            snapshot = await asyncio.to_thread(nerv.tasks.snapshot, sid, cursor)
            cursor = snapshot["cursor"]
            yield "id: " + str(cursor) + "\ndata: " + json.dumps(snapshot) + "\n\n"
            await asyncio.sleep(config.ROBOT_EVENTS_POLL_S)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/sessions/{sid}/task-model")
def task_model(sid: str, inp: TaskModelIn):
    raise HTTPException(409, "Select a decision model in a control scheme")


@app.post("/api/sessions/{sid}/estop")
def estop_session(sid: str) -> dict:
    """Emergency stop that keeps the pose: the body latches its joints and stops thinking. Not a power cut."""
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.estop(sid)


@app.post("/api/sessions/{sid}/release")
def release_session(sid: str) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.release(sid)


@app.post("/api/sessions/{sid}/reset")
def reset_session_world(sid: str) -> dict:
    """Reset the whole simulated scene, including furniture; preserve the time preset."""
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.reset_world(sid)


@app.get("/api/sessions/{sid}/scene/frame")
def scene_test_frame(sid: str, owner: str, token: str, epoch: str):
    try:
        client, credentials = nerv.scene_tests.credentials(sid, dict(owner=owner, token=token, epoch=epoch))
        content, stamp = client.scene_frame(credentials)
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Sim-Time": stamp})
    except FileNotFoundError:
        raise HTTPException(404, "No such session")
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error))
    except httpx.HTTPError:
        raise HTTPException(503, "The scene camera is unavailable")


@app.get("/api/sessions/{sid}/scene/{resource}")
def scene_test_read(sid: str, resource: str):
    if resource not in ("state", "catalogue"):
        raise HTTPException(404, "Unknown scene resource")
    try:
        return nerv.scene_tests.read(sid, resource)
    except FileNotFoundError:
        raise HTTPException(404, "No such session")
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error))
    except httpx.HTTPError:
        raise HTTPException(503, "The world is unavailable")


@app.post("/api/sessions/{sid}/scene/{operation}")
def scene_test_operation(sid: str, operation: str, payload: dict):
    try:
        if operation == "acquire":
            return nerv.scene_tests.acquire(sid, str(payload.get("owner", "")))
        if operation not in ("heartbeat", "release", "command"):
            raise HTTPException(404, "Unknown scene operation")
        return nerv.scene_tests.action(sid, operation, payload)
    except FileNotFoundError:
        raise HTTPException(404, "No such session")
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error))
    except httpx.HTTPError:
        raise HTTPException(503, "The world is unavailable")


@app.post("/api/sessions/{sid}/brain")
def set_brain(sid: str, inp: BrainIn) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    if inp.brain == "operator" or inp.brain not in {b["name"] for b in list_brains()}:
        raise HTTPException(400, messages.UNKNOWN_BRAIN_REPLY)
    if sid in nerv._runs:
        raise HTTPException(409, "Finish the Agent reply before changing its model")
    try:
        ModelRegistry().require_key(inp.brain)
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error)) from error
    if nerv.store.get(sid).brain == "operator":
        raise HTTPException(409, "Create a separate Agent session from Home")
    ModelRegistry().remember(inp.brain)
    nerv.store.set_brain(sid, inp.brain)
    nerv.store.append(sid, {"role": "brain_divider", "brain": inp.brain})
    return {"ok": True}


@app.post("/api/sessions/{sid}/arm")
def arm(sid: str, inp: ArmIn) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.arm(sid, inp.armed)


@app.get("/api/sessions/{sid}/capabilities")
def session_capabilities(sid: str) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    entries = nerv.tool_sheet(sid)
    return {domain: [entry for entry in entries if entry["domain"] == domain]
            for domain in ("agent_tools", "robot_actions")}


@app.get("/api/sessions/{sid}/tools")
def session_tools(sid: str) -> list:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.tool_sheet(sid)


@app.post("/api/sessions/{sid}/teleop")
def teleop(sid: str, inp: TeleopIn) -> StreamingResponse:
    """Operator remote control: call one body verb or tool function directly (gated, logged, SSE)."""
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")

    def gen():
        for ev in slog.bound_stream(sid, nerv.teleop_stream(sid, inp.name, inp.arguments)):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _chat_ready(sid):
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    selected = nerv.store.get(sid).brain
    from ..vlm.errors import ModelError
    try:
        ModelRegistry().require_key(selected)
    except ModelError as error:
        raise HTTPException(400, error.public()) from None
    except KeyError:
        raise HTTPException(400, messages.BRAIN_NOT_CONFIGURED_REPLY) from None


# ---- chat ------------------------------------------------------------------------------------
@app.post("/api/chat")
def chat(inp: ChatIn) -> dict:
    _chat_ready(inp.session)
    out = nerv.handle(inp.session, inp.text)
    return {"reply": out["reply"], "stop_reason": out["stop_reason"], "error": out.get("error")}


@app.post("/api/chat/stream")
def chat_stream(inp: ChatIn) -> StreamingResponse:
    _chat_ready(inp.session)

    def gen():
        for ev in slog.bound_stream(inp.session, nerv.handle_stream(inp.session, inp.text)):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/perceive")
def perceive(session: str = Query(...)) -> dict:
    if not nerv.store.exists(session):
        raise HTTPException(404, "no such session")
    obs = nerv.perceive(session)
    if obs is None:
        return {"images": [], "state": {}}
    return {"state": obs.state,
            "images": [{"name": i["name"], "b64": base64.b64encode(i["png"]).decode()} for i in obs.images]}


@app.get("/api/imgfile")
def imgfile(ref: str = Query(...)) -> FileResponse:
    root = os.path.abspath(nerv.store.root)
    p = os.path.abspath(os.path.join(root, ref))
    if not p.startswith(root + os.sep) or not os.path.isfile(p):
        raise HTTPException(404, "no such image")
    return FileResponse(p, media_type="image/png")


# ---- status / config / signals ------------------------------------------------------------
@app.get("/api/status")
def status() -> dict:
    return {"version": __version__, "nodes": len(nerv.launcher.nodes), "data_root": paths.DATA_ROOT}




@app.put("/api/config")
def update_config(payload: dict):
    from .user_settings import save
    try:
        save(payload)
        return api_config()
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error))


@app.post("/api/config/reset")
def reset_config():
    from .user_settings import save
    save({}, reset=True)
    return api_config()


@app.get("/api/robot")
def robot_display():
    from .robot_profile import read
    return read()


@app.put("/api/robot")
def robot_display_save(payload: dict):
    from .robot_profile import save_name
    try:
        return save_name(payload.get("name"))
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get("/api/robot/image")
def robot_image():
    from .robot_profile import root
    from fastapi.responses import FileResponse
    path = root() / "portrait.webp"
    if not path.exists():
        raise HTTPException(404, "No custom robot image")
    return FileResponse(path, media_type="image/webp")


@app.put("/api/robot/image")
async def robot_image_save(request: Request):
    from .robot_profile import MAX_IMAGE_BYTES, save_image
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image must be at most 5 MB")
    try:
        return save_image(bytes(content))
    except Exception:
        raise HTTPException(400, "Choose a valid JPEG, PNG or WebP image up to 20 megapixels")


@app.delete("/api/robot/image")
def robot_image_reset():
    from .robot_profile import reset_image
    return reset_image()


@app.post("/api/operator/workspace")
def operator_workspace(inp: NewSessionIn):
    if not inp.body:
        raise HTTPException(400, "Select a robot for the operator workspace")
    try:
        from .session import require_scene
        require_scene(inp.body, inp.world, inp.mode)
        for session in nerv.store.all():
            if session.brain == "operator" and session.body == inp.body and session.mode == inp.mode and session.world == inp.world and session.status == "active":
                return session.summary()
        return nerv.new_session("operator", inp.body, inp.world, [], [], inp.mode, inp.world_configuration, operator_workspace=True)
    except (ValueError, RuntimeError, KeyError) as error:
        raise HTTPException(400, str(error))


@app.get("/api/config")
def api_config() -> dict:
    from .user_settings import describe
    return {"params": describe(), "trust_all": trust.trust_all_enabled()}


@app.get("/api/nerv")
def nerv_dashboard() -> dict:
    return {"nodes": nodes(), "sessions": nerv.store.list(), "brains": list_brains(),
            "registry": nerv.registry.summary(), "recent": slog.recent_ring()[-50:]}


@app.get("/api/nerv/events")
def nerv_events(since: int = 0) -> StreamingResponse:
    def gen():
        last = since
        while not _shutdown_requested.is_set():
            for e in slog.recent_ring(last):
                last = e["id"]
                yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
            _shutdown_requested.wait(config.SIGNAL_POLL_INTERVAL_S)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/session-logs")
def session_logs(session: str = "", limit: int = 300) -> dict:
    return {"sessions": slog.sessions(), "entries": slog.recent(limit=limit, session=session)}


# ---- static web app (frontend/out) -------------------------------------------------------
_WEB = paths.WEB_ROOT
if os.path.isdir(_WEB):
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
else:
    @app.get("/")
    def home() -> Response:
        return Response("NERV backend is up. Build the web app (frontend) or use the CLI.", media_type="text/plain")
