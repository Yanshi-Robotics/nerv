"""HTTP Project integration without creating a world or preparing hardware."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from nerv.platform.hub import Nerv
from nerv.platform.project_api import router, shutdown
from nerv.platform.session import SessionStore


@pytest.fixture
def api(tmp_path):
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    app = FastAPI()
    app.include_router(router(lambda: hub))
    with TestClient(app) as client:
        yield client, hub
    shutdown(hub)


def test_create_and_open_without_keys_or_runtime(api):
    client, hub = api
    response = client.post("/api/projects", json={"name": "Workbench", "mode": "sim"})
    assert response.status_code == 200
    project = response.json()
    detail = client.post(f"/api/projects/{project['id']}/open").json()
    assert detail["project"]["name"] == "Workbench"
    assert detail["active_run"] is None
    assert not detail["readiness"]["ready"]
    assert not hub.launcher.nodes and not hub.store.all()
    assert client.get("/api/projects").json()[0]["id"] == project["id"]


def test_mcp_credential_cannot_create_operator_project(api):
    client, _ = api
    response = client.post("/api/projects", json={"name": "Blocked", "mode": "sim"},
                           headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 403
    assert client.get("/api/projects").json() == []


def test_unsupported_type_rejected_and_version_required(api):
    client, _ = api
    assert client.post("/api/projects", json={"name": "Future", "mode": "sim2real"}).status_code == 409
    project = client.post("/api/projects", json={"name": "Present", "mode": "real"}).json()
    endpoint = f"/api/projects/{project['id']}"
    assert client.patch(endpoint, json={"name": "No version"}).status_code == 409
    assert client.patch(endpoint, json={"name": "Renamed", "expected_revision": 1}).json()["revision"] == 2


def test_agent_pairing_redaction_revocation_and_scope(api):
    client, _ = api
    project = client.post("/api/projects", json={"name": "Present", "mode": "sim"}).json()
    paired = client.post("/api/agent-connections", json={"name": "Fixture Agent", "projects": [project["id"]],
                                                       "permissions": ["observe"]})
    assert paired.status_code == 200
    value = paired.json()
    token = value["token"]
    assert token not in client.get("/api/agent-connections").text
    headers = {"Authorization": "Bearer " + token}
    assert client.post("/api/agent/invoke", headers=headers,
                       json={"operation": "projects_list", "arguments": {}}).status_code == 200
    result = client.post("/api/agent/invoke", headers=headers,
                         json={"operation": "run_start", "arguments": {"project_id": project["id"], "request_id": "test"}})
    assert result.status_code == 403
    cid = value["connection"]["id"]
    assert client.post(f"/api/agent-connections/{cid}/revoke").status_code == 200
    assert client.post("/api/agent/invoke", headers=headers,
                       json={"operation": "projects_list", "arguments": {}}).status_code == 403
