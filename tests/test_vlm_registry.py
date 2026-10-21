"""Provider protocol fixtures use no network, billing, credentials or robot hardware."""
import json
from types import SimpleNamespace

import pytest

from nerv.brain.providers.base import LLMReply, ToolCall
from nerv.brain.providers.responses import ResponsesLLM
from nerv.vlm.store import ModelRegistry, VisualModel


def test_visual_only_registry_private_credentials_and_effort_isolation(tmp_path, monkeypatch):
    registry = ModelRegistry(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry.credentials.set("OPENAI_API_KEY", "fixture-secret")
    assert registry.credentials.path.stat().st_mode & 0o077 == 0
    assert "fixture-secret" not in json.dumps(registry.list())
    model = registry.model("openai-sol").model_dump()
    model["input_modalities"] = ["text"]
    with pytest.raises(ValueError, match="documented image"):
        VisualModel(**model)
    registry.select("openai-sol", "xhigh")
    registry.select("deepseek-flash", "max")
    models = {model["id"]: model for model in registry.list()}
    assert models["openai-sol"]["effort"] == "xhigh"
    with pytest.raises(ValueError, match="does not support"):
        registry.select("deepseek-flash", "medium")
    assert models["openai-sol"]["available"]
    assert not models["deepseek-flash"]["available"]


def test_validation_is_explicit_and_tied_to_effort_and_credentials(tmp_path, monkeypatch):
    registry = ModelRegistry(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry.credentials.set("OPENAI_API_KEY", "fixture-secret")
    monkeypatch.setattr("secrets.token_hex", lambda _: "ABCDEF")
    calls = []
    class Provider:
        def chat(self, system, history, tools, images):
            calls.append((tools, images))
            return LLMReply("", [ToolCall("probe", "vlm_probe", {"code":"ABCDEF"})]) if tools else LLMReply("Verified")
    assert len(calls) == 0
    result = registry.validate("openai-sol", Provider())
    assert all(result[key] == "passed" for key in ("image", "tools", "continuation"))
    assert next(model for model in registry.list() if model["id"] == "openai-sol")["available"]
    registry.select("openai-sol", "high")
    current = next(model for model in registry.list() if model["id"] == "openai-sol")
    assert current["available"] and current["checks"] == {}
    assert len(calls) == 2


def test_responses_wire_format_effort_and_continuation(tmp_path):
    model = ModelRegistry(tmp_path).model("openai-sol")
    requests = []
    output = [{"type":"reasoning", "id":"reason", "encrypted_content":"private-state"}, {"type":"function_call", "call_id":"call", "name":"move", "arguments":"{}"}]
    class Response:
        def model_dump(self, **_):
            return {"status":"completed", "output": output, "usage":{"input_tokens":12, "output_tokens":3, "total_tokens":15}}
    class Stream:
        def __iter__(self):
            yield SimpleNamespace(type="response.completed", response=Response())
        def close(self):
            pass
    def create(**request):
        requests.append(request)
        return Stream()
    provider = ResponsesLLM(model, "unused", "high", SimpleNamespace(responses=SimpleNamespace(create=create)))
    first = provider.chat("system", [{"role":"user", "text":"start"}], [], None)
    assert requests[0]["reasoning"] == {"effort":"high"}
    assert requests[0]["store"] is False and first.usage["input"] == 12
    wire = provider.inputs("system", [{"role":"assistant", "text":"", "tool_calls":first.tool_calls, "continuation":first.continuation}, {"role":"tool", "id":"call", "name":"move", "content":"running"}], None)
    assert wire[1:3] == output
    other = ResponsesLLM(model, "unused", "low", provider.client)
    assert not any(item.get("encrypted_content") for item in other.inputs("system", [{"role":"assistant", "text":"", "tool_calls":first.tool_calls, "continuation":first.continuation}], None))
    before = len(requests)
    with pytest.raises(InterruptedError):
        provider.chat("system", [], [], None, cancelled=lambda:True)
    assert len(requests) == before


def test_physical_acceptance_is_separate_and_configuration_specific(tmp_path, monkeypatch):
    registry = ModelRegistry(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry.credentials.set("OPENAI_API_KEY", "fixture-secret")
    signature = registry.request_signature(registry.model("openai-sol"))
    with pytest.raises(ValueError, match="API checks"):
        registry.record_acceptance("openai-sol", signature, "passed", "fixture:operator-evidence", "fixture-binding")
    registry.record_acceptance("openai-sol", signature, "pending", "fixture:pending-not-a-physical-test", "fixture-binding")
    assert next(m for m in registry.list("fixture-binding") if m["id"] == "openai-sol")["physical"]["status"] == "pending"
    registry.select("openai-sol", "high")
    assert next(m for m in registry.list() if m["id"] == "openai-sol")["physical"]["status"] == "unverified"


def test_rejected_credential_input_is_not_echoed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    with TestClient(server.app) as client:
        response = client.put("/api/credentials/FIXTURE_KEY", json={"value":["secret-that-must-not-be-reflected"]})
    assert response.status_code == 422
    assert "secret-that-must-not-be-reflected" not in response.text


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_production_chat_rejects_a_legacy_mock_session(tmp_path, monkeypatch, endpoint):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    session, _ = hub.store.new("mock", None, None)
    monkeypatch.setattr(server, "nerv", hub)
    with TestClient(server.app) as client:
        response = client.post(endpoint, json={"session":session.id, "text":"start"})
    assert response.status_code == 400
    assert not hub.store.get(session.id).messages
