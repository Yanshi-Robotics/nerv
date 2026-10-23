"""Offline configuration fixtures: no provider requests or physical devices."""
import json
from types import SimpleNamespace

import pytest

from nerv.brain.providers.base import LLMReply
from nerv.vlm.errors import ModelError, model_error
from nerv.vlm.store import ModelRegistry

ANSWER = {"left": {"shape": "circle", "color": "red"}, "right": {"shape": "square", "color": "blue"}}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    choices = iter(("circle", "red", "square", "blue"))
    monkeypatch.setattr("nerv.vlm.probe.secrets.choice", lambda _: next(choices))
    return ModelRegistry(tmp_path / "private")


def provider(registry, answer=ANSWER, callback=lambda: None):
    def factory(model_id, *, candidate_key, effort):
        assert effort is None
        assert candidate_key == "candidate-fixture"
        def chat(system, history, tools, images, *, cancelled):
            assert not tools and len(images) == 1
            assert images[0]["png"].startswith(b"\x89PNG")
            assert json.dumps(ANSWER) not in json.dumps(history)
            callback()
            return LLMReply(json.dumps(answer))
        return SimpleNamespace(chat=chat)
    return factory


def test_candidate_only_saved_after_correct_visual_answer(registry):
    result = registry.configure("openai-sol", "candidate-fixture", provider_factory=provider(
        registry, callback=lambda: pytest.raises(ModelError, registry.require_key, "openai-sol")))
    assert result["saved"]
    assert "image" not in result and "answer" not in result
    assert registry.credentials.resolve("OPENAI_API_KEY") == "candidate-fixture"
    assert registry.credentials.path.stat().st_mode & 0o077 == 0
    entries = {m["id"]: m for m in registry.list()}
    assert entries["openai-sol"]["available"] and entries["openai-astra"]["available"]
    assert not entries["openai-astra"]["checks"]
    assert "candidate-fixture" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["wrong", "cancel", "concurrent", "storage", "timeout"])
def test_failed_candidate_keeps_previous_key(registry, monkeypatch, failure):
    registry.credentials.set("OPENAI_API_KEY", "old-fixture")
    cancelled = False
    def callback():
        nonlocal cancelled
        if failure == "cancel":
            cancelled = True
        if failure == "concurrent":
            registry.credentials.set("OPENAI_API_KEY", "newer-fixture")
        if failure == "storage":
            monkeypatch.setattr("nerv.vlm.store.private_write", lambda *_: (_ for _ in ()).throw(PermissionError()))
        if failure == "timeout":
            raise TimeoutError("do not expose candidate-fixture")
    result = registry.configure("openai-sol", "candidate-fixture", cancelled=lambda: cancelled,
                                provider_factory=provider(registry, {} if failure == "wrong" else ANSWER, callback))
    assert not result["saved"]
    assert registry.credentials.resolve("OPENAI_API_KEY") == ("newer-fixture" if failure == "concurrent" else "old-fixture")
    assert "candidate-fixture" not in json.dumps(result)


def test_empty_key_prevents_all_session_side_effects(tmp_path, monkeypatch):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    monkeypatch.setattr(hub.launcher, "ensure_world", lambda *_: pytest.fail("launched before key check"))
    with pytest.raises(ModelError, match="Configure an API key"):
        hub.new_session("openai-sol", "unresolved-body", "unresolved-world")
    assert not hub.store.all() and hub._bound_runtime is None


def test_registered_key_creates_session_without_diagnostics(tmp_path, monkeypatch):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    registry = ModelRegistry()
    registry.credentials.set("OPENAI_API_KEY", "expired-but-registered-fixture")
    monkeypatch.setattr(registry, "validate", lambda *_: pytest.fail("unexpected paid validation"))
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    created = hub.new_session("openai-sol", None, None, tools=[])
    assert created["brain"] == "openai-sol"
    assert registry.list()[0]["checks"] == {}


@pytest.mark.parametrize("status,provider_code,expected", [
    (401, "", "key_invalid"), (402, "", "quota_exceeded"), (429, "insufficient_quota", "quota_exceeded"),
    (429, "", "rate_limited"), (404, "", "model_unavailable"), (400, "", "request_unsupported"),
    (503, "", "provider_unavailable"), (408, "", "request_timeout"),
])
def test_error_codes_do_not_echo_sdk_payload(status, provider_code, expected):
    error = RuntimeError("fixture-secret")
    error.status_code = status
    error.body = {"error": {"code": provider_code, "message": "fixture-secret"}}
    public = model_error(error).public()
    assert public["code"] == expected
    assert "fixture-secret" not in json.dumps(public)


def test_http_rejects_direct_key_write(monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    monkeypatch.setattr(server, "_models_idle", lambda: None)
    response = TestClient(server.app).put("/api/credentials/OPENAI_API_KEY", json={"value": "fixture-secret"})
    assert response.status_code == 400
    assert not ModelRegistry().credentials.describe("OPENAI_API_KEY")["configured"]


def test_http_configuration_and_missing_session_key(registry, monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    monkeypatch.setattr(server, "_models_idle", lambda: None)
    monkeypatch.setattr(server, "ModelRegistry", lambda: registry)
    monkeypatch.setattr(registry, "provider", provider(registry))
    client = TestClient(server.app)
    before = len(server.nerv.store.all())
    missing = client.post("/api/sessions", json={"brain": "openai-sol", "body": "unresolved-body", "world": "unresolved-world"})
    assert missing.status_code == 400 and missing.json()["detail"]["code"] == "key_missing"
    assert len(server.nerv.store.all()) == before
    result = client.post("/api/vlm/openai-sol/configure", json={"value": "candidate-fixture"})
    assert result.status_code == 200 and result.json()["saved"]
    assert "candidate-fixture" not in result.text


def test_changed_model_does_not_save_candidate(registry):
    registry.credentials.set("OPENAI_API_KEY", "old-fixture")
    def change():
        spec = registry.model("openai-sol").model_dump()
        spec["request_timeout_s"] += 1
        registry.upsert(spec)
    result = registry.configure("openai-sol", "candidate-fixture", provider_factory=provider(registry, callback=change))
    assert result["error"]["code"] == "configuration_changed"
    assert registry.credentials.resolve("OPENAI_API_KEY") == "old-fixture"


def test_cancel_before_dispatch_never_calls_provider(registry):
    result = registry.configure("openai-sol", "candidate-fixture", cancelled=lambda: True,
                                provider_factory=lambda *a, **kw: pytest.fail("cancelled test dispatched"))
    assert result["error"]["code"] == "configuration_cancelled"
    assert not registry.credentials.describe("OPENAI_API_KEY")["configured"]


def test_public_errors_have_both_translations():
    from pathlib import Path
    from nerv.vlm.errors import MESSAGES
    root = Path(__file__).resolve().parents[1]
    for language in ("en", "zh"):
        strings = json.loads((root / "frontend" / "locales" / f"{language}.json").read_text())["strings"]
        assert set(MESSAGES.values()) <= strings.keys()


def test_missing_runtime_is_not_reported_as_preparable(monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    monkeypatch.setattr(server.nerv.store, "get", lambda _: SimpleNamespace(body="fixture"))
    monkeypatch.setattr(server.nerv, "body_client", lambda _: None)
    response = TestClient(server.app).get("/api/sessions/offline-fixture/runtime")
    assert response.status_code == 503 and response.json()["detail"] == "body node offline"


def test_read_only_storage_prevents_billable_request(registry, monkeypatch):
    monkeypatch.setattr(registry.credentials, "check_writable", lambda: (_ for _ in ()).throw(PermissionError()))
    result = registry.configure("openai-sol", "candidate-fixture",
                                provider_factory=lambda *a, **kw: pytest.fail("paid request before storage check"))
    assert result["error"]["code"] == "storage_failed"
    assert not result["saved"] and not registry.credentials.path.exists()


def test_storage_probe_leaves_no_key_or_temporary_files(registry):
    registry.credentials.check_writable()
    assert not registry.credentials.path.exists()
    assert list(registry.root.iterdir()) == []
    assert registry.root.stat().st_mode & 0o077 == 0


def test_registration_uses_provider_default_effort(registry):
    registry.select("openai-sol", "high")
    result = registry.configure("openai-sol", "candidate-fixture", provider_factory=provider(registry))
    assert result["saved"]
    assert registry._read()["efforts"]["openai-sol"] == "high"
