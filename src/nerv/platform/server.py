"""NERV/Operator over HTTP + SSE. The web app is a client of this and nothing else."""
from __future__ import annotations

import base64
import json
import os
import time
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
app = FastAPI(title="NERV", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def private_validation_error(_request, error):
    # Never reflect a rejected credential or request body into a 422 response.
    return JSONResponse(status_code=422, content={"detail": [
        {key: value for key, value in item.items() if key in ("loc", "msg", "type")}
        for item in error.errors()]})


@app.on_event("shutdown")
def _shutdown() -> None:
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
    if nerv._runs or any(c.post("/executions/status").get("running") for c in nerv._bodies.values()):
        raise HTTPException(409, "Finish the active task before changing model configuration")


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
    registry = ModelRegistry()
    try:
        registry.credentials.set(reference, payload.value)
        registry.invalidate_credential(reference)
        nerv._brains.clear()
        return {"ok": True, **registry.credentials.describe(reference)}
    except (ValueError, PermissionError) as error:
        raise HTTPException(400, str(error))


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
    if sid in nerv._runs:
        raise HTTPException(409, "finish the active task before preparing or loading")
    if operation not in ("prepare", "policies/load"):
        raise HTTPException(404, "unknown runtime operation")
    client = nerv.body_client(session.body)
    result = client.post("/" + operation, payload)
    client.refresh()
    return result


@app.get("/api/sessions/{sid}/runtime")
def runtime_status(sid: str):
    session = nerv.store.get(sid)
    client = nerv.body_client(session.body) if session.body else None
    return client.status() if client else {"prepared": False}


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
            "family": raw.family, "sensors": raw.sensors}


@app.post("/api/nodes/{key:path}/approve")
def node_approve(key: str) -> dict:
    c = nerv.node_client(key)
    if c is None:
        raise HTTPException(404, "no such node")
    h = c.approve()
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
        if (inp.brain or _default_brain()) not in {b["name"] for b in list_brains()}:
            raise ValueError("select a registered visual model")
        return nerv.new_session(inp.brain or _default_brain(), inp.body, inp.world, inp.sensors, inp.agent_tools, inp.mode, inp.world_configuration)
    except (ValueError, KeyError, RuntimeError, TimeoutError, FileNotFoundError) as e:
        raise HTTPException(400, e.args[0] if e.args else str(e))


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
        while nerv.store.exists(sid) and not await request.is_disconnected():
            snapshot = await asyncio.to_thread(nerv.tasks.snapshot, sid, cursor)
            cursor = snapshot["cursor"]
            yield "id: " + str(cursor) + "\ndata: " + json.dumps(snapshot) + "\n\n"
            await asyncio.sleep(config.ROBOT_EVENTS_POLL_S)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/sessions/{sid}/task-model")
def task_model(sid: str, inp: TaskModelIn):
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    registry = ModelRegistry()
    try:
        model = registry.model(inp.model)
        if inp.effort is not None and inp.effort not in model.efforts:
            raise ValueError("Unsupported reasoning effort")
        if nerv.tasks.snapshot(sid)["task"] and nerv.tasks.snapshot(sid)["task"]["status"] in (
                "starting", "running", "updating", "stopping"):
            raise ValueError("Finish the current task before changing its model")
        with nerv.store.transaction():
            session = nerv.store.get(sid)
            if session.status != "active":
                raise ValueError("Task model settings require an active session")
            session.task_model, session.task_effort = inp.model, inp.effort
            nerv.store.save(session)
        return {"ok": True}
    except (KeyError, ValueError) as error:
        raise HTTPException(409, str(error))


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
    if inp.brain not in {b["name"] for b in list_brains()}:
        raise HTTPException(400, messages.UNKNOWN_BRAIN_REPLY)
    if sid in nerv._runs:
        raise HTTPException(409, "Finish the Agent reply before changing its model")
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
    if not any(model["name"] == selected and model["available"] for model in list_brains()):
        raise HTTPException(400, messages.BRAIN_NOT_CONFIGURED_REPLY)


# ---- chat ------------------------------------------------------------------------------------
@app.post("/api/chat")
def chat(inp: ChatIn) -> dict:
    _chat_ready(inp.session)
    out = nerv.handle(inp.session, inp.text)
    return {"reply": out["reply"], "stop_reason": out["stop_reason"]}


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


@app.get("/api/config")
def api_config() -> dict:
    return {"params": config.runtime_params(), "trust_all": trust.trust_all_enabled()}


@app.get("/api/nerv")
def nerv_dashboard() -> dict:
    return {"nodes": nodes(), "sessions": nerv.store.list(), "brains": list_brains(),
            "registry": nerv.registry.summary(), "recent": slog.recent_ring()[-50:]}


@app.get("/api/nerv/events")
def nerv_events(since: int = 0) -> StreamingResponse:
    def gen():
        last = since
        while True:
            for e in slog.recent_ring(last):
                last = e["id"]
                yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
            time.sleep(config.SIGNAL_POLL_INTERVAL_S)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/session-logs")
def session_logs(session: str = "", limit: int = 300) -> dict:
    return {"sessions": slog.sessions(), "entries": slog.recent(limit=limit, session=session)}


# ---- static web app (frontend/out) -------------------------------------------------------
_WEB = os.path.join(paths.REPO_ROOT, "frontend", "out")
if os.path.isdir(_WEB):
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
else:
    @app.get("/")
    def home() -> Response:
        return Response("NERV backend is up. Build the web app (frontend) or use the CLI.", media_type="text/plain")
