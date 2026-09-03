"""The body node: NERV/Body over MCP plus the control plane over HTTP.

Wraps a family implementation (a BodyImpl) and exposes:
  MCP  tools/list, tools/call (with progress), resources nerv://observation | nerv://config |
       nerv://capabilities, prompt "guidance"
  HTTP /health, /status, /config (GET/POST: armed …), /stream (MJPEG of the first camera),
       /sensors, /stop, /
Arming lives here as a second switch under the platform's: while the node is disarmed every
mutating tool is refused, whatever the platform said. Real-hardware buses start disarmed.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import json
import time
import uuid
from typing import Any, Protocol

import anyio
import anyio.from_thread
import anyio.to_thread
import mcp.types as t
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..nerve.body import (CAPABILITIES_URI, CONFIG_URI, GUIDANCE_PROMPT, MUTATING_KINDS,
                          NON_MUTATING_KINDS, OBSERVATION_URI)
from .skills import StopFlag


class BodyImpl(Protocol):
    name: str
    family: str
    version: str

    def tools(self) -> list[dict]: ...                       # [{name, description, parameters, kind}]

    def observe(self) -> tuple[dict, list[tuple[str, bytes]]]: ...   # state, [(stream, png)]

    def invoke(self, name: str, *, _progress=None, **args) -> dict: ...

    def status(self) -> dict: ...

    def stream_jpeg(self) -> bytes | None: ...

    def sensor_names(self) -> list[str]: ...

    def config_options(self) -> list[dict]: ...

    def set_option(self, key: str, value: str) -> dict: ...

    def close(self) -> None: ...


class BodyNode:
    def __init__(self, impl: BodyImpl, *, guidance: str, stop: StopFlag, armed: bool,
                 world: str = "", bus_kind: str = "") -> None:
        self.impl = impl
        self.guidance = guidance
        self.stop = stop
        self.armed = armed
        self.world = world
        self.bus_kind = bus_kind
        self.epoch = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self._kinds = {td["name"]: td.get("kind", "primitive") for td in impl.tools()}

    # -- config (armed + family options) -------------------------------------------------
    def config(self) -> dict:
        opts = [{"key": "armed", "label": "Armed", "value": "true" if self.armed else "false",
                 "description": "Only an armed body carries out mutating actions.",
                 "choices": [{"value": "true", "label": "armed"}, {"value": "false", "label": "disarmed"}]}]
        opts += list(self.impl.config_options() or [])
        return {"options": opts}

    def set_config(self, key: str, value: str) -> dict:
        if key == "armed":
            self.armed = str(value).strip().lower() in ("1", "true", "yes", "on", "armed")
            return {"ok": True, "message": f"body {'armed' if self.armed else 'disarmed'}",
                    "armed": self.armed}
        return self.impl.set_option(key, value)

    def invoke(self, name: str, *, _progress=None, **args) -> dict:
        kind = self._kinds.get(name, "primitive")
        if kind in MUTATING_KINDS and not self.armed:
            return {"ok": False, "message": ("the body is not armed, so it will not move. "
                                             "The operator arms it on the body node's page "
                                             "or through NERV.")}
        return self.impl.invoke(name, _progress=_progress, **args)


def build_app(node: BodyNode, cors_origins: list[str]) -> FastAPI:
    impl = node.impl
    srv = Server(impl.name)

    @srv.list_tools()
    async def _list_tools():
        out = []
        for td in impl.tools():
            kind = td.get("kind", "primitive")
            out.append(t.Tool(name=td["name"], description=td.get("description", ""),
                              inputSchema=td.get("parameters") or {"type": "object", "properties": {}},
                              annotations=t.ToolAnnotations(readOnlyHint=(kind in NON_MUTATING_KINDS))))
        return out

    @srv.call_tool()
    async def _call_tool(name, arguments):
        kwargs = dict(arguments or {})
        ctx = srv.request_context
        token = ctx.meta.progressToken if ctx.meta else None
        session, req_id = ctx.session, ctx.request_id

        def _report(progress: float, message: str = "") -> None:
            if token is None:
                return
            anyio.from_thread.run(functools.partial(
                session.send_progress_notification, token, float(progress), total=1.0,
                message=str(message) or None, related_request_id=req_id))

        res = await anyio.to_thread.run_sync(functools.partial(node.invoke, name, _progress=_report, **kwargs))
        if not isinstance(res, dict):
            res = {"ok": True, "message": str(res)}
        ok = bool(res.get("ok", True))
        msg = res.get("message", "") or ("ok" if ok else "failed")
        return t.CallToolResult(content=[t.TextContent(type="text", text=msg)],
                                structuredContent=res.get("data") or None, isError=not ok)

    @srv.list_resources()
    async def _list_resources():
        return [
            t.Resource(uri=OBSERVATION_URI, name="observation", mimeType="application/json",
                       description="what the body senses right now: state JSON, then image blobs"),
            t.Resource(uri=CONFIG_URI, name="config", mimeType="application/json",
                       description="what is configurable and what it is set to (change it over HTTP)"),
            t.Resource(uri=CAPABILITIES_URI, name="capabilities", mimeType="application/json",
                       description="family, tool kinds (read/primitive/skill), sensor streams"),
        ]

    @srv.read_resource()
    async def _read_resource(uri):
        u = str(uri)
        if u == CONFIG_URI:
            return [ReadResourceContents(content=json.dumps(node.config()), mime_type="application/json")]
        if u == CAPABILITIES_URI:
            meta = {"family": impl.family, "version": impl.version,
                    "tools": {td["name"]: {"kind": td.get("kind", "primitive")} for td in impl.tools()},
                    "sensors": impl.sensor_names(), "armed": node.armed, "epoch": node.epoch}
            return [ReadResourceContents(content=json.dumps(meta), mime_type="application/json")]
        state, images = await anyio.to_thread.run_sync(impl.observe)
        state = dict(state or {})
        state["cameras"] = [n for n, b in images if b]
        state["armed"] = node.armed
        out = [ReadResourceContents(content=json.dumps(state, default=str), mime_type="application/json")]
        for _n, blob in images:
            if blob:
                out.append(ReadResourceContents(content=blob, mime_type="image/png"))
        return out

    @srv.list_prompts()
    async def _list_prompts():
        return [t.Prompt(name=GUIDANCE_PROMPT, description="the body's own description of itself")] \
            if node.guidance else []

    @srv.get_prompt()
    async def _get_prompt(name, arguments):
        return t.GetPromptResult(description="guidance", messages=[
            t.PromptMessage(role="user", content=t.TextContent(type="text", text=node.guidance))])

    sm = StreamableHTTPSessionManager(app=srv, json_response=False, stateless=True)

    async def mcp_asgi(scope, receive, send):
        await sm.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with sm.run():
            try:
                yield
            finally:
                try:
                    impl.close()
                except Exception:
                    pass

    app = FastAPI(title=f"NERV body · {impl.name}", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.mount("/mcp", mcp_asgi)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "node": "body", "body": impl.name, "family": impl.family,
                "version": impl.version, "world": node.world, "bus": node.bus_kind,
                "armed": node.armed, "epoch": node.epoch}

    @app.get("/status")
    def status() -> dict:
        return impl.status()

    @app.get("/sensors")
    def sensors() -> dict:
        return {"sensors": impl.sensor_names()}

    @app.get("/config")
    def get_config() -> dict:
        return node.config()

    @app.post("/config")
    def set_config(body: dict) -> dict:
        key = str(body.get("key", ""))
        if not key or "value" not in body:
            return {"ok": False, "message": "give key and value"}
        return node.set_config(key, str(body.get("value")))

    @app.post("/stop")
    def stop() -> dict:
        node.stop.request()
        return {"ok": True, "message": "stop requested"}

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def gen():
            while True:
                jpg = impl.stream_jpeg()
                if jpg is not None:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                await asyncio.sleep(1 / 12)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/snapshot")
    def snapshot() -> Response:
        jpg = impl.stream_jpeg()
        return Response(content=jpg or b"", media_type="image/jpeg", status_code=200 if jpg else 503)

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (f"<html><body style='font-family:system-ui;margin:2rem'><h2>NERV body · {impl.name}</h2>"
                f"<p>family {impl.family} · world {node.world or '-'} · bus {node.bus_kind} · "
                f"armed <b id=a>{node.armed}</b></p>"
                f"<p><button onclick=\"fetch('/config',{{method:'POST',headers:{{'content-type':'application/json'}},"
                f"body:JSON.stringify({{key:'armed',value:'true'}})}}).then(()=>location.reload())\">Arm</button> "
                f"<button onclick=\"fetch('/config',{{method:'POST',headers:{{'content-type':'application/json'}},"
                f"body:JSON.stringify({{key:'armed',value:'false'}})}}).then(()=>location.reload())\">Disarm</button> "
                f"<button onclick=\"fetch('/stop',{{method:'POST'}})\">Stop</button></p>"
                f"<img src='/stream' style='max-width:640px;border:1px solid #ccc'>"
                f"<p><a href='/status'>/status</a> · <a href='/config'>/config</a> · <a href='/sensors'>/sensors</a></p>"
                f"</body></html>")

    return app


def jpeg_from_png(png: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def png_from_rgb(arr: Any) -> bytes:
    from PIL import Image
    im = Image.fromarray(arr)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
