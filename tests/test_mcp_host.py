"""Offline MCP/HTTP bridge checks; not acceptance of third-party Agent applications."""
import asyncio
import json

import httpx
import pytest

from nerv.platform.mcp_host import HostBridge, content_blocks, publish_host


def prepare(tmp_path):
    (tmp_path / "agent-connections" / "clients").mkdir(parents=True)
    client = "agent_123"
    (tmp_path / "agent-connections" / f"{client}.json").write_text(json.dumps({"revoked": False}))
    (tmp_path / "agent-connections" / "clients" / f"{client}.json").write_text(json.dumps({"token": "private"}))
    (tmp_path / "agent-host.json").write_text(json.dumps({"url": "http://127.0.0.1:8170", "channel": "stable", "instance_id": "instance"}))
    return client


def test_bridge_validates_local_host_and_uses_explicit_auth(tmp_path):
    prepare(tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"projects": []})
    bridge = HostBridge(root=tmp_path, transport=httpx.MockTransport(handler))
    async def run():
        assert await bridge.invoke("projects_list", {}) == {"projects": []}
        await bridge.close()
    asyncio.run(run())
    assert requests[0].headers["authorization"] == "Bearer private"
    assert requests[0].headers["x-nerv-host-instance"] == "instance"
    assert requests[0].url.path == "/api/agent/invoke"


def test_remote_url_and_missing_pairing_fail_closed(tmp_path):
    prepare(tmp_path)
    path = tmp_path / "agent-host.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "url": "https://example.com"}))
    with pytest.raises(ValueError, match="Invalid local"):
        HostBridge(root=tmp_path)
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="credential"):
        HostBridge(root=tmp_path, client_id="agent_absent")


def test_host_restart_does_not_silently_reconnect(tmp_path):
    prepare(tmp_path)
    bridge = HostBridge(root=tmp_path)
    path = tmp_path / "agent-host.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "instance_id": "replacement"}))
    async def run():
        with pytest.raises(ValueError, match="changed"):
            await bridge.invoke("projects_list", {})
        await bridge.close()
    asyncio.run(run())


def test_auth_failure_never_echoes_secret(tmp_path):
    prepare(tmp_path)
    bridge = HostBridge(root=tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"detail": "private"})))
    async def run():
        with pytest.raises(PermissionError) as error:
            await bridge.invoke("projects_list", {})
        assert "private" not in str(error.value)
        await bridge.close()
    asyncio.run(run())


def test_images_are_native_mcp_content():
    content = content_blocks({"receipt_id": "r", "images": [{"name": "camera", "mimeType": "image/png", "data": "aW1hZ2U="}]})
    assert content[-1].type == "image"
    assert "aW1hZ2U=" not in content[0].text
    assert json.loads(content[0].text)["receipt_id"] == "r"


def test_receipt_only_published_for_loopback(tmp_path):
    assert publish_host(tmp_path, "0.0.0.0", 8170) is None
    assert not (tmp_path / "agent-host.json").exists()
    result = publish_host(tmp_path, "127.0.0.1", 8123)
    assert result["url"].endswith(":8123")


def test_real_stdio_handshake_and_tool_schemas(tmp_path):
    """SDK stdio protocol with an inert transport heartbeat fixture; no robot/model."""
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    prepare(tmp_path)
    env = {k: os.environ[k] for k in ("PATH", "PYTHONPATH", "SYSTEMROOT", "HOME") if k in os.environ}
    # Convert relative test PYTHONPATH to absolute, independent of client cwd.
    from pathlib import Path
    env["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in env.get("PYTHONPATH", "").split(os.pathsep) if p)
    env.update(NERV_HOME=str(tmp_path), NERV_STORAGE_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    program = """
import asyncio
from nerv.platform import mcp_host
async def inert_transport(self, action):
    return {'transport_id':'fixture', 'brain_instance_id':'brain_fixture', 'heartbeat_s':1}
mcp_host.HostBridge.transport = inert_transport
asyncio.run(mcp_host.serve('stable', 'agent_123', 'brain_fixture'))
"""
    params = StdioServerParameters(command=sys.executable, args=["-c", program], env=env)
    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.serverInfo.name == "nerv-host"
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {"observe", "task_start", "action", "task_cancel"} <= names
                assert not {"arm", "shell", "install", "connect_robot"} & names
                invalid = await session.call_tool("activity", {"project_id": "p", "text": "x", "source": "runtime"})
                assert invalid.isError
    asyncio.run(run())


def test_retired_chat_entries_have_no_host_side_effect(monkeypatch, capsys):
    from nerv.platform import cli
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "_hub", lambda: pytest.fail("Retired entry created a Host"))
    assert cli.cmd_chat(SimpleNamespace()) == 2
    assert cli.cmd_run(SimpleNamespace()) == 2
    assert cli.cmd_session(SimpleNamespace(action="new")) == 2
    assert "Project" in capsys.readouterr().err
