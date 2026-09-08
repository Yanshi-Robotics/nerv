"""NERV/Operator over HTTP + SSE. The web app is a client of this and nothing else."""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Optional

import httpx

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, config, paths
from ..brain.providers import list_brains
from . import messages, trust
from . import explore
from .hub import Nerv
from .session import log as slog

nerv = Nerv()
app = FastAPI(title="NERV", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.on_event("shutdown")
def _shutdown() -> None:
    nerv.shutdown()


class NewSessionIn(BaseModel):
    brain: Optional[str] = None
    body: Optional[str] = None
    world: Optional[str] = None
    sensors: list[str] = []
    tools: Optional[list[str]] = None


class ChatIn(BaseModel):
    session: str
    text: str


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
    brains = {b["name"]: b for b in list_brains()}
    d = brains.get(config.DEFAULT_BRAIN)
    if d is None or d["available"]:
        return config.DEFAULT_BRAIN
    return next((n for n, b in brains.items() if b["available"]), config.DEFAULT_BRAIN)


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
                item["tools"] = [t.name for t in caps.tools]
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
    return {"key": key, "url": c.base, "manifest": trust.manifest(c.base, raw.tools, raw.guidance),
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
        return nerv.new_session(inp.brain or _default_brain(), inp.body, inp.world, inp.sensors, inp.tools)
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
    return {"ok": nerv.store.delete(sid)}


@app.post("/api/sessions/{sid}/interrupt")
def interrupt_session(sid: str) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    nerv.stop(sid)
    return {"ok": True}


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
    nerv.store.set_brain(sid, inp.brain)
    nerv.store.append(sid, {"role": "brain_divider", "brain": inp.brain})
    return {"ok": True}


@app.post("/api/sessions/{sid}/arm")
def arm(sid: str, inp: ArmIn) -> dict:
    if not nerv.store.exists(sid):
        raise HTTPException(404, "no such session")
    return nerv.arm(sid, inp.armed)


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


# ---- chat ------------------------------------------------------------------------------------
@app.post("/api/chat")
def chat(inp: ChatIn) -> dict:
    if not nerv.store.exists(inp.session):
        raise HTTPException(404, "no such session")
    out = nerv.handle(inp.session, inp.text)
    return {"reply": out["reply"], "stop_reason": out["stop_reason"]}


@app.post("/api/chat/stream")
def chat_stream(inp: ChatIn) -> StreamingResponse:
    if not nerv.store.exists(inp.session):
        raise HTTPException(404, "no such session")

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
