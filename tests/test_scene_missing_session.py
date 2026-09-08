"""A closed old browser tab must not turn missing scene sessions into server errors."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("method,suffix", [("GET", "state"), ("GET", "catalogue"),
    ("GET", "frame?owner=old&token=old&epoch=old"), ("POST", "acquire"),
    ("POST", "heartbeat"), ("POST", "release"), ("POST", "command")])
def test_missing_session_is_a_normal_not_found(monkeypatch, method, suffix):
    from nerv.platform import server
    from nerv.platform.scene_tests import SceneTests
    hub = SimpleNamespace(store=SimpleNamespace(get=Mock(side_effect=FileNotFoundError)),
        world_client=Mock())
    scene = SceneTests(hub)
    monkeypatch.setattr(server, "nerv", SimpleNamespace(scene_tests=scene))
    client = TestClient(server.app)
    arguments = {"json": {"owner": "old", "token": "old", "epoch": "old"}} if method == "POST" else {}
    response = client.request(method, f"/api/sessions/old/scene/{suffix}", **arguments)
    assert response.status_code == 404
    assert response.json()["detail"] == "No such session"
    hub.world_client.assert_not_called()
