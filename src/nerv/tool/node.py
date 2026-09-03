"""A minimal NERV/Tool node: tools/list + tools/call over MCP, /health over HTTP."""
from __future__ import annotations

import contextlib
import functools

import anyio
import anyio.to_thread
import mcp.types as t
from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager


def build_tool_app(name: str, tools: list[dict], call, version: str = "0") -> FastAPI:
    """tools: [{name, description, parameters}]; call(name, args) -> {"ok","message","data"}"""
    srv = Server(name)

    @srv.list_tools()
    async def _list_tools():
        return [t.Tool(name=td["name"], description=td.get("description", ""),
                       inputSchema=td.get("parameters") or {"type": "object", "properties": {}},
                       annotations=t.ToolAnnotations(readOnlyHint=True)) for td in tools]

    @srv.call_tool()
    async def _call_tool(tool_name, arguments):
        res = await anyio.to_thread.run_sync(functools.partial(call, tool_name, dict(arguments or {})))
        ok = bool(res.get("ok", True))
        return t.CallToolResult(content=[t.TextContent(type="text", text=res.get("message", "") or ("ok" if ok else "failed"))],
                                structuredContent=res.get("data") or None, isError=not ok)

    sm = StreamableHTTPSessionManager(app=srv, json_response=False, stateless=True)

    async def asgi(scope, receive, send):
        await sm.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with sm.run():
            yield

    app = FastAPI(title=f"NERV tool · {name}", lifespan=lifespan)
    app.mount("/mcp", asgi)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "node": "tool", "tool": name, "version": version,
                "tools": [td["name"] for td in tools]}

    return app
