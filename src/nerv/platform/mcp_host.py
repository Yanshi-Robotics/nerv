"""Local stdio MCP bridge. Does not create a Host, model or robot connection."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit
import uuid

import httpx

from ..install_system import atomic_json
from ..managed import channel_home, storage_root
from ..brain_core.core import OPERATIONS


def publish_host(home, host, port):
    """Called only after the bound server reports readiness."""
    if host not in ("127.0.0.1", "::1", "localhost"):
        return None
    home = Path(home)
    from .. import paths
    channel = "preview" if home.resolve() == channel_home("preview", storage_root(paths.NERV_HOME)).resolve() else "stable"
    hostname = "[::1]" if host == "::1" else host
    value = {"url": f"http://{hostname}:{port}", "channel": channel, "instance_id": uuid.uuid4().hex}
    path = home / "agent-host.json"
    atomic_json(path, value)
    path.chmod(0o600)
    return value


class HostBridge:
    def __init__(self, channel="stable", client_id=None, *, root=None, transport=None, brain_id=None):
        self.brain_id = brain_id
        home = channel_home(channel, storage_root(root))
        self.path = home / "agent-host.json"
        try:
            self.host = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raise ValueError("NERV Host is not running. Start the selected channel and pair this Agent in Settings.") from None
        url = urlsplit(self.host.get("url", ""))
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1", "localhost"}
                or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")
                or self.host.get("stopped") or self.host.get("channel") != channel or not self.host.get("instance_id")):
            raise ValueError("Invalid local Host receipt; restart the selected NERV channel")
        credentials = home / "agent-connections" / "clients"
        if client_id is None:
            candidates = []
            for file in credentials.glob("agent_*.json"):
                try:
                    public = json.loads((credentials.parent / file.name).read_text())
                    if not public.get("revoked"):
                        candidates.append(file.stem)
                except (OSError, ValueError):
                    continue
            if len(candidates) != 1:
                raise ValueError("Select a paired Agent with --client ID; create it in NERV Settings first")
            client_id = candidates[0]
        if not client_id.startswith("agent_") or not client_id[6:].isalnum():
            raise ValueError("Invalid connection ID")
        try:
            self.token = json.loads((credentials / f"{client_id}.json").read_text())["token"]
        except (OSError, ValueError, KeyError):
            raise ValueError("No paired Agent credential exists; pair this Agent in NERV Settings") from None
        self.client = httpx.AsyncClient(base_url=self.host["url"], trust_env=False, follow_redirects=False,
                                       timeout=httpx.Timeout(60, connect=5), transport=transport)

    async def invoke(self, operation, arguments):
        # Never silently reconnect a bridge to a different Host instance.
        try:
            current = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raise ValueError("NERV Host stopped; restart this MCP connection after NERV is ready") from None
        if current.get("stopped") or current.get("instance_id") != self.host["instance_id"]:
            raise ValueError("NERV Host changed; reconnect and inspect the project before continuing")
        try:
            response = await self.client.post("/api/agent/invoke", json={"operation": operation, "arguments": arguments, "brain_instance_id": self.brain_id},
                headers={"Authorization": "Bearer " + self.token, "X-Nerv-Host-Instance": self.host["instance_id"]})
        except httpx.HTTPError:
            raise ValueError("NERV Host is unavailable; request outcome may be unknown. Inspect task state before retrying.") from None
        if response.status_code in (401, 403):
            raise PermissionError("Agent access was refused; review pairing and project permissions in NERV")
        if response.status_code >= 500:
            raise ValueError("NERV could not complete this request. Inspect task state before retrying.")
        if response.status_code >= 300:
            detail = response.json().get("detail", "NERV rejected the request")
            raise ValueError(detail if isinstance(detail, str) else "NERV rejected invalid request arguments")
        return response.json()

    async def close(self):
        await self.client.aclose()


def content_blocks(result):
    """Images are MCP image content, never giant JSON strings or embedded truth views."""
    from mcp.types import ImageContent, TextContent
    metadata = dict(result)
    images = metadata.pop("images", [])
    if images:
        metadata["images"] = [{"name": im["name"], "mimeType": im["mimeType"]} for im in images]
    blocks = [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False, allow_nan=False))]
    for im in images:
        blocks.append(TextContent(type="text", text="Sensor: " + im["name"]))
        blocks.append(ImageContent(type="image", data=im["data"], mimeType=im["mimeType"]))
    return blocks


async def serve(channel="stable", client_id=None, brain_id=None):
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool
    from jsonschema import validate
    from jsonschema.exceptions import ValidationError

    bridge = HostBridge(channel, client_id, brain_id=brain_id)
    server = Server("nerv-host")

    @server.list_tools()
    async def list_tools():
        return [Tool(name=name, description=description, inputSchema=schema)
                for name, (description, schema) in OPERATIONS.items()]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            if name not in OPERATIONS:
                raise ValueError("Unknown NERV operation")
            validate(arguments or {}, OPERATIONS[name][1])
            return content_blocks(await bridge.invoke(name, arguments or {}))
        except ValidationError:
            raise ValueError("Invalid NERV tool arguments") from None
        except (ValueError, PermissionError) as error:
            # The SDK renders protocol/tool errors. No credentials enter the message.
            raise ValueError(str(error)) from None
        except Exception:
            raise ValueError("NERV request failed; inspect its task state before retrying.") from None

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await bridge.close()


def main(channel="stable", client_id=None, brain_id=None):
    # MCP stdout is reserved for protocol frames.
    import sys
    try:
        asyncio.run(serve(channel, client_id, brain_id))
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
