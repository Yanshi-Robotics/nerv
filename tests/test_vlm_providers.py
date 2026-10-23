"""Offline provider/secret regression tests. All API traffic uses MockTransport."""
import json
from types import SimpleNamespace

import httpx
import pytest

from nerv.brain.providers.anthropic_messages import AnthropicMessagesLLM
from nerv.brain.providers.gemini import GeminiLLM
from nerv.brain.providers.responses import ResponsesLLM
from nerv.nerve.body import ToolSpec
from nerv.vlm.store import CredentialStore, ModelRegistry, PROVIDERS, VisualModel, private_write

TOOL = ToolSpec("inspect", "Inspect a test code", {"type": "object", "properties": {"code": {"type": "string"}}}, "read")
IMAGES = [{"name": "top", "png": b"top-fixture"}, {"name": "wrist", "png": b"wrist-fixture"}]


def test_private_store_ignores_environment_and_remains_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old-project-key")
    store = CredentialStore(tmp_path / "private")
    assert not store.resolve("OPENAI_API_KEY")
    assert store.describe("OPENAI_API_KEY") == {"configured": False, "source": None, "writable": True}
    store.set("OPENAI_API_KEY", "new-private-key")
    assert store.resolve("OPENAI_API_KEY") == "new-private-key"
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    store.set("OPENAI_API_KEY", None)
    assert not store.resolve("OPENAI_API_KEY")
    assert "old-project-key" == __import__("os").environ["OPENAI_API_KEY"]


def test_private_storage_rejects_symlinks_and_project_home(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "private")
    store.path.parent.mkdir()
    target = tmp_path / "do-not-change"
    target.write_text("{}")
    store.path.symlink_to(target)
    with pytest.raises(PermissionError):
        store.set("OPENAI_API_KEY", "fixture")
    from nerv import paths
    monkeypatch.setenv("NERV_HOME", str(__import__("pathlib").Path(paths.REPO_ROOT) / "private-models"))
    with pytest.raises(ValueError, match="outside the project"):
        ModelRegistry()
    assert target.read_text() == "{}"


def test_catalogue_has_five_providers_and_only_documented_vlms(tmp_path):
    registry = ModelRegistry(tmp_path)
    models = registry.list()
    assert {m["provider"] for m in models} == set(PROVIDERS)
    assert len(models) == 9
    assert all("image" in m["input_modalities"] and m["evidence"].startswith("https://") for m in models)
    assert not any(m["available"] for m in models)
    assert len(registry.providers()) == 5
    model = registry.model("gemini-flash").model_dump()
    model["protocol"] = "responses"
    with pytest.raises(ValueError, match="must match"):
        VisualModel(**model)
    registry.select("gemini-flash-lite", "minimal")
    registry.select("grok-47", "xhigh")
    assert next(m for m in registry.list() if m["id"] == "gemini-flash-lite")["effort"] == "minimal"
    with pytest.raises(ValueError, match="does not support"):
        registry.select("gemini-flash", "minimal")
    with pytest.raises(ValueError, match="does not support"):
        registry.select("grok-47", "none")


def test_stale_receipt_is_not_displayed_as_a_current_pass(tmp_path):
    registry = ModelRegistry(tmp_path)
    registry.credentials.set("OPENAI_API_KEY", "fixture-key")
    model = registry.model("openai-sol")
    data = registry._read()
    receipt = {"signature": registry.request_signature(model), "image": "passed",
               "tools": "passed", "continuation": "passed"}
    data["checks"][model.id] = receipt
    private_write(registry.path, data)
    assert next(m for m in registry.list() if m["id"] == model.id)["available"]
    registry.select(model.id, "high")
    current = next(m for m in registry.list() if m["id"] == model.id)
    assert current["available"] and current["checks"] == {}
    # Preserve historical evidence on disk without displaying it as current.
    assert registry._read()["checks"][model.id] == receipt


@pytest.mark.parametrize("model_id", ["deepseek-flash", "openai-sol", "grok-47"])
def test_responses_dialects_preserve_state_and_native_effort(tmp_path, model_id):
    requests = []
    output = [{"type": "reasoning", "id": "r", "encrypted_content": "opaque"},
              {"type": "function_call", "call_id": "c", "name": "inspect", "arguments": '{"code":"ok"}'}]
    class Stream:
        closed = False
        def __iter__(self):
            payload = {"status": "completed", "output": output, "usage": {"input_tokens": 12, "output_tokens": 3}}
            yield SimpleNamespace(type="response.completed", response=SimpleNamespace(model_dump=lambda **_: payload))
        def close(self):
            self.closed = True
    stream = Stream()
    def create(**request):
        requests.append(request)
        return stream
    spec = ModelRegistry(tmp_path).model(model_id)
    provider = ResponsesLLM(spec, "fixture", "high", SimpleNamespace(responses=SimpleNamespace(create=create)))
    reply = provider.chat("system", [{"role": "user", "text": "inspect"}], [TOOL], IMAGES)
    assert reply.tool_calls[0].arguments == {"code": "ok"} and stream.closed
    request = requests[0]
    assert request["reasoning"] == {"effort": "high"}
    assert len([p for p in request["input"][-1]["content"] if p["type"] == "input_image"]) == 2
    if model_id == "deepseek-flash":
        assert "store" not in request and "include" not in request
    else:
        assert request["store"] is False and request["include"] == ["reasoning.encrypted_content"]
    history = [{"role": "assistant", "text": "", "tool_calls": reply.tool_calls, "continuation": reply.continuation},
               {"role": "tool", "id": "c", "name": "inspect", "content": "done"}]
    assert provider.inputs("system", history, None)[1:3] == output
    with pytest.raises(InterruptedError):
        provider.chat("system", [], [], None, cancelled=lambda: True)
    assert len(requests) == 1


def sse(events):
    return "".join(f"event: {item['event_type']}\ndata: {json.dumps(item)}\n\n" for item in events).encode()


def test_gemini_native_sdk_stream_tool_signature_and_followup(tmp_path):
    from google import genai
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        events = [
            {"event_type": "step.start", "index": 0, "step": {"type": "thought"}},
            {"event_type": "step.delta", "index": 0, "delta": {"type": "thought_signature", "signature": "c2ln"}},
            {"event_type": "step.delta", "index": 0, "delta": {"type": "thought_summary", "content": {"type": "text", "text": "inspect"}}},
            {"event_type": "step.stop", "index": 0},
            {"event_type": "step.start", "index": 1, "step": {"type": "function_call", "id": "call", "name": "inspect", "arguments": {}}},
            {"event_type": "step.delta", "index": 1, "delta": {"type": "arguments_delta", "arguments": '{"code":'}},
            {"event_type": "step.delta", "index": 1, "delta": {"type": "arguments_delta", "arguments": '"ok"}'}},
            {"event_type": "step.stop", "index": 1},
            {"event_type": "interaction.status_update", "interaction_id": "i", "status": "requires_action", "usage": {"total_input_tokens": 12, "total_output_tokens": 3, "total_tokens": 15}},
        ] if len(requests) == 1 else [
            {"event_type": "step.start", "index": 0, "step": {"type": "model_output", "content": []}},
            {"event_type": "step.delta", "index": 0, "delta": {"type": "text", "text": "Acknowledged"}},
            {"event_type": "step.stop", "index": 0},
            {"event_type": "interaction.completed", "interaction": {"id": "i2", "status": "completed"}},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(events))
    client = genai.Client(api_key="fixture-only", http_options={"client_args": {"transport": httpx.MockTransport(handle)}})
    provider = GeminiLLM(ModelRegistry(tmp_path).model("gemini-flash"), "fixture-only", "medium", client)
    try:
        first = provider.chat("system", [{"role": "user", "text": "inspect"}], [TOOL], IMAGES)
        assert first.finish_status == "completed" and first.usage["total"] == 15
        assert first.tool_calls[0].arguments == {"code": "ok"}
        assert first.continuation["items"][0]["signature"] == "c2ln"
        history = [{"role": "user", "text": "inspect"},
                   {"role": "assistant", "text": "", "tool_calls": first.tool_calls, "continuation": first.continuation},
                   {"role": "tool", "id": "call", "name": "inspect", "content": "done"}]
        second = provider.chat("system", history, [], None)
        assert second.text == "Acknowledged" and not second.tool_calls
        assert requests[0]["store"] is False
        assert requests[0]["generation_config"]["thinking_level"] == "medium"
        assert len([p for p in requests[0]["input"][-1]["content"] if p["type"] == "image"]) == 2
        assert requests[1]["input"][1:3] == first.continuation["items"]
        assert requests[1]["input"][-1]["type"] == "function_result"
        other = GeminiLLM(provider.spec, "other-key", "medium", client)
        assert not any(s["type"] == "thought" for s in other.inputs(history, None))
        with pytest.raises(InterruptedError):
            provider.chat("system", [], [], None, cancelled=lambda: True)
        assert len(requests) == 2
    finally:
        client.close()


def test_anthropic_native_sdk_preserves_signed_blocks_and_rejects_truncation(tmp_path):
    from anthropic import Anthropic
    import httpx2
    requests = []
    payload = {"id": "msg", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
               "content": [{"type": "thinking", "thinking": "private", "signature": "signed"},
                           {"type": "tool_use", "id": "c", "name": "inspect", "input": {"code": "ok"}}],
               "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}}
    def handle(request):
        requests.append(json.loads(request.content))
        # Anthropic SDK's streaming accumulator is exercised with real SSE events.
        events = [
            ("message_start", {"type": "message_start", "message": {**payload, "content": [], "stop_reason": None}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": payload["content"][0]}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "c", "name": "inspect", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"code":"ok"}'}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use" if len(requests) == 1 else "max_tokens", "stop_sequence": None}, "usage": {"output_tokens": 5}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        data = "".join(f"event: {kind}\ndata: {json.dumps(body)}\n\n" for kind, body in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=data)
    client = Anthropic(api_key="fixture-only", http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    provider = AnthropicMessagesLLM(ModelRegistry(tmp_path).model("claude-opus"), "fixture-only", "xhigh", client)
    try:
        first = provider.chat("system", [{"role": "user", "text": "inspect"}], [TOOL], IMAGES)
        assert first.finish_status == "completed" and first.tool_calls[0].arguments == {"code": "ok"}
        assert requests[0]["output_config"] == {"effort": "xhigh"}
        assert len([b for b in requests[0]["messages"][-1]["content"] if b["type"] == "image"]) == 2
        history = [{"role": "assistant", "text": "", "tool_calls": first.tool_calls, "continuation": first.continuation},
                   {"role": "tool", "id": "c", "name": "inspect", "content": "done"}]
        assert provider.inputs(history, None)[0]["content"][0]["signature"] == "signed"
        assert provider.chat("system", history, [], None).finish_status == "incomplete"
        other = AnthropicMessagesLLM(provider.spec, "other-key", "xhigh", client)
        assert not any(b["type"] == "thinking" for m in other.inputs(history, None) for b in m["content"])
        with pytest.raises(InterruptedError):
            provider.chat("system", [], [], None, cancelled=lambda: True)
        assert len(requests) == 2
    finally:
        client.close()


@pytest.mark.parametrize("model_id,adapter", [("gemini-flash", GeminiLLM), ("claude-opus", AnthropicMessagesLLM)])
def test_native_stream_cancel_closes_and_never_returns_tools(tmp_path, model_id, adapter):
    state = {"cancelled": False, "closed": False}
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *_): self.close()
        def __iter__(self):
            state["cancelled"] = True
            yield SimpleNamespace()
        def close(self): state["closed"] = True
    stream = Stream()
    client = SimpleNamespace(interactions=SimpleNamespace(create=lambda **_: stream),
                             messages=SimpleNamespace(stream=lambda **_: stream))
    provider = adapter(ModelRegistry(tmp_path).model(model_id), "fixture", client=client)
    with pytest.raises(InterruptedError):
        provider.chat("system", [], [], None, cancelled=lambda: state["cancelled"])
    assert state["closed"]


def test_settings_endpoint_does_not_leak_keys_or_trigger_model_requests(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    monkeypatch.setenv("NERV_HOME", str(tmp_path / "private"))
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-must-not-be-used")
    monkeypatch.setattr(ModelRegistry, "provider", lambda *_: pytest.fail("unexpected paid request"))
    with TestClient(server.app) as client:
        result = client.put("/api/credentials/OPENAI_API_KEY", json={"value": "fixture-secret"})
        assert result.status_code == 400
        # Existing credentials remain usable without a new probe on discovery.
        ModelRegistry().credentials.set("OPENAI_API_KEY", "fixture-secret")
        listing = client.get("/api/vlm")
        assert listing.status_code == 200
        assert "fixture-secret" not in listing.text and "legacy-must-not-be-used" not in listing.text
        assert len(listing.json()["providers"]) == 5
        assert any(m["available"] for m in listing.json()["models"])
        assert client.post("/api/vlm/openai-sol/select", json={"effort": "high"}).status_code == 200
        assert client.delete("/api/credentials/OPENAI_API_KEY").status_code == 405
        assert client.put("/api/credentials/OPENAI_API_KEY", json={"value": None}).status_code == 200
        assert not ModelRegistry().credentials.resolve("OPENAI_API_KEY")
