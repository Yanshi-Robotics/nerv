import threading

from nerv.brain.providers.base import LLMReply, ToolCall
from nerv.harness.agent import AgentHarness
from nerv.harness.context import ContextManager
from nerv.platform.gate import SafetyGate
from nerv.platform.router import Turn
from nerv.platform.session import SessionStore


def test_late_provider_reply_cannot_dispatch_or_commit(tmp_path):
    entered, finish, invalid = threading.Event(), threading.Event(), threading.Event()

    class DelayedProvider:
        model, vision = "test-provider", True

        def chat(self, *_):
            entered.set()
            assert finish.wait(2)
            return LLMReply("late", [ToolCall("late", "set_core_task", {"task": "must not commit"})])

    store = SessionStore(str(tmp_path))
    session, _ = store.new("test", None, None)
    brain = AgentHarness(DelayedProvider(), "test", ContextManager(10000, 100, 100))
    events = []
    turn = Turn(store, session, brain, None, None, [], SafetyGate(), events.append,
                current=lambda: not invalid.is_set())
    worker = threading.Thread(target=turn.run, args=("hello",))
    worker.start()
    assert entered.wait(2)
    invalid.set()
    finish.set()
    worker.join(2)
    assert not worker.is_alive()
    assert store.get(session.id).core_task == ""
    assert not any(e["type"] == "tool_call" for e in events)
    assert not any(m.get("text") == "late" for m in store.get(session.id).messages)


def test_restart_marks_unfinished_run_unknown_without_replay(tmp_path):
    from nerv.harness.memory import SessionMemory
    memory = SessionMemory(tmp_path, "s_test")
    memory.append("run_started", {"run_id": "r1"})
    memory.recover_runs()
    memory.recover_runs()
    interrupted = [e for e in memory.events() if e["kind"] == "run_interrupted"]
    assert len(interrupted) == 1 and interrupted[0]["data"]["outcome"] == "unknown"


def test_incomplete_response_is_recorded_but_never_replayed_as_live_continuation(tmp_path):
    class Provider:
        model, vision = "fixture", True
        def chat(self, *_):
            return LLMReply("unfinished", [ToolCall("partial", "move", {})], {"total":10},
                            {"identity":"fixture", "items":[{"type":"function_call", "call_id":"partial"}]}, "incomplete")
    store = SessionStore(str(tmp_path))
    session, _ = store.new("test", None, None)
    brain = AgentHarness(Provider(), "fixture", ContextManager(10000, 100, 100))
    turn = Turn(store, session, brain, None, None, [], SafetyGate(), lambda _:None)
    turn.run("hello")
    saved = store.get(session.id).messages
    assert not any(message.get("tool_calls") for message in saved)
    assert not any(message.get("continuation") for message in saved)
    responses = [event for event in store.memory(session.id).events() if event["kind"] == "model_response"]
    assert responses[0]["data"]["finish_status"] == "incomplete"
    assert responses[0]["data"]["continuation"]["items"][0]["call_id"] == "partial"
