"""CPU regression tests for journal recovery and execution ownership."""
import threading
from types import SimpleNamespace

import pytest

from nerv.brain.providers.base import LLMReply
from nerv.harness.agent import AgentHarness
from nerv.harness.context import ContextManager
from nerv.platform.gate import SafetyGate
from nerv.platform.hub import Nerv
from nerv.platform.router import Turn
from nerv.platform.session import SessionStore, context
from nerv_runtime_sdk.control import DispatchAuthority, ManagedExecutions, StopFlag
from nerv_runtime_sdk.node import BodyNode
from test_body_node import FakeArm


def test_snapshot_failure_does_not_hide_journal(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr("nerv.platform.session.store.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(OSError):
            store.new("test", None, None)
    recovered = store.all()
    assert len(recovered) == 1 and store.exists(recovered[0].id)


def test_unknown_interrupted_tool_result_keeps_source_for_compaction(tmp_path):
    store = SessionStore(str(tmp_path))
    session, _ = store.new("test", None, None)
    store.append(session.id, {"role": "user", "text": "old" * 2000})
    store.append(session.id, {"role": "assistant", "text": "", "tool_calls": [{"id": "old", "name": "move", "arguments": {}}]})
    store.append(session.id, {"role": "user", "text": "new"})
    store.append(session.id, {"role": "assistant", "text": "current"})
    history = context.build(store.get(session.id).messages, token_budget=100000)
    assert all("seq" in message for message in history)
    manager = ContextManager(1200, 100, 100)
    _, compacted = manager.prepare("system", history, [], [], store.memory(session.id), lambda *_: ("Old action outcome unknown.", "fixture", {}))
    assert compacted[0]["role"] == "user" and "unknown" in store.memory(session.id).summary()["text"]


def test_stale_exception_cannot_stop_the_current_robot(tmp_path):
    current = threading.Event()
    current.set()
    calls = []
    class FailingBrain:
        model = "fixture"
        def run_turn(self, *_):
            current.clear()
            raise TimeoutError("old request failed")
    store = SessionStore(str(tmp_path))
    session, _ = store.new("test", None, None)
    body = SimpleNamespace(post=lambda path: calls.append(path))
    turn = Turn(store, session, FailingBrain(), body, None, [], SafetyGate(), lambda _: None, current=current.is_set)
    turn.run("hello")
    assert calls == []


def test_old_turn_never_inherits_replacement_token(tmp_path):
    store = SessionStore(str(tmp_path))
    session, _ = store.new("mock", None, None)
    hub = Nerv(store=store)
    old, new = threading.Event(), threading.Event()
    hub._runs[session.id] = new
    turn = hub._turn(session.id, lambda _: None, old)
    old.set()
    assert not turn.is_current()


def test_cancel_finalization_blocks_new_start_until_its_hold_is_done():
    dispatch = DispatchAuthority()
    dispatch.enable(True)
    manager = ManagedExecutions(dispatch, supervision_timeout_s=2, cancel_timeout_s=.01, poll_s=.01)
    holding, finish_hold, finish_inference = threading.Event(), threading.Event(), threading.Event()
    holds = []
    def hold():
        holding.set()
        assert finish_hold.wait(2)
        holds.append("held-old")
    manager.on_stop = hold
    old = manager.start("old", lambda *_: finish_inference.wait(2))
    canceller = threading.Thread(target=lambda: manager.cancel(old["execution_id"]))
    canceller.start()
    assert holding.wait(1)
    # The old cancellation owns the lifecycle lock until its hold is acknowledged.
    assert not manager._lock.acquire(blocking=False)
    finish_hold.set()
    finish_inference.set()
    canceller.join(2)
    manager.wait(old["execution_id"], 1)
    manager.on_stop = lambda: holds.append("held-new")
    new = manager.start("new", lambda token, stop: stop.wait(1))
    assert new["running"] and holds == ["held-old"]
    manager.cancel(new["execution_id"])


def test_late_control_request_rejected_after_stop_and_after_new_claim():
    impl = FakeArm()
    node = BodyNode(impl, guidance="fixture", stop=StopFlag(), armed=True, require_control=True)
    node._control_token = "old"
    node.invalidate_control()
    node._control_token = "new"
    with pytest.raises(InterruptedError):
        node.invoke("set_angle", deg=10, _nerv_control="old")
    assert impl.calls == []


def test_builtin_cancel_reaches_its_actual_runner():
    from nerv.runtime.builtin import BuiltinRuntime
    runtime = BuiltinRuntime(SimpleNamespace(id="fixture", family="test"), None)
    runtime._impl = object()
    runtime.stop = StopFlag()
    assert runtime.cancel_current()["ok"] and runtime.stop.is_set()


def test_model_without_tool_calls_cannot_abandon_an_active_skill(tmp_path):
    class Provider:
        model, vision = "fixture", True
        def chat(self, *_):
            return LLMReply("done")
    calls = []
    link = SimpleNamespace(is_current=lambda: len(calls) < 2, observe=lambda: None, model_tools=lambda: [], system_prompt=lambda: "Offline fixture", model_phase=lambda _: None, memory=lambda: SimpleNamespace(flush=lambda: None, summary=lambda: None, append=lambda *_: None), raw_history=lambda: [], registers=lambda: {}, system_context=lambda: {"body":None}, model_reply=lambda _: None, executing=lambda: True, think=lambda _: None, supervise_wait=lambda: calls.append("supervised"), stop_reason=lambda: None, say=lambda _: calls.append("abandoned"))
    AgentHarness(Provider(), "fixture", ContextManager(10000, 100, 100)).run_turn(link, None)
    assert calls == ["supervised", "supervised"]


def test_delayed_control_claim_cannot_replace_new_generation():
    from fastapi.testclient import TestClient
    from nerv_runtime_sdk.node import build_app
    node = BodyNode(FakeArm(), guidance="fixture", stop=StopFlag(), armed=True, require_control=True)
    with TestClient(build_app(node, [])) as client:
        assert client.post("/control/claim", json={"owner":"controller", "generation":2, "token":"new"}).json()["ok"]
        assert not client.post("/control/claim", json={"owner":"controller", "generation":1, "token":"old"}).json()["ok"]
        assert node._control_token == "new"


def test_installed_package_versions_match_source():
    from importlib.metadata import requires, version
    from nerv import __version__
    assert version("nerv") == __version__
    # Host UI/provider releases do not change the independently installed wire SDK.
    dependency = f"nerv-runtime-sdk=={version('nerv-runtime-sdk')}"
    assert dependency in requires("nerv")
    assert dependency in requires("nerv-runtime-lerobot")


def test_context_overflow_pauses_with_a_clear_reply_and_preserves_raw_history(tmp_path):
    from nerv.harness.context import ContextOverflow
    class Brain:
        model = "fixture"
        def run_turn(self, *_):
            raise ContextOverflow("fixture budget exhaustion")
    store = SessionStore(str(tmp_path))
    session, _ = store.new("test", None, None)
    calls = []
    turn = Turn(store, session, Brain(), SimpleNamespace(post=lambda path: calls.append(path)), None,
                [], SafetyGate(), lambda _: None)
    outcome = turn.run("preserve this request")
    assert "Task paused" in outcome["reply"] and "context budget" in outcome["reply"]
    assert calls == ["/stop"]
    assert store.get(session.id).messages[0]["text"] == "preserve this request"
