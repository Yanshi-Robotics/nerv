"""Explicit offline fixtures: scheduling evidence, not model or robot competence."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nerv.harness.agent import AgentHarness
from nerv.harness.context import ContextManager
from nerv.nerve.brain import Say
from nerv.platform.hub import Nerv
from nerv.platform.session import SessionStore


class WaitingBrain:
    model = name = "offline-task-fixture"
    vision = False

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def run_turn(self, link, user):
        self.entered.set()
        self.release.wait(3)
        link.say(Say("Fixture controller ended; no task success claim"))


@pytest.fixture
def task_host(tmp_path, monkeypatch):
    hub = Nerv(store=SessionStore(str(tmp_path)))
    session, _ = hub.store.new("fixture", "fixture-robot", "fixture-world", epoch="epoch")
    robot = Mock()
    monkeypatch.setattr(hub, "body_client", lambda _name: robot)
    monkeypatch.setattr(hub, "world_client", lambda *_: None)
    monkeypatch.setattr(hub, "_control_error", lambda _: None)
    monkeypatch.setattr(hub, "claim_robot", Mock(return_value="control"))
    monkeypatch.setattr(hub, "stop_robot", Mock(return_value={"ok": True}))
    brain = WaitingBrain()
    monkeypatch.setattr(hub, "new_role_model", lambda *_: brain)
    yield hub, session, brain
    for token in hub.tasks._tokens.values():
        token.set()
    brain.release.set()
    for thread in hub.tasks._threads.values():
        thread.join(3)


def test_reply_cancel_and_progress_do_not_cancel_task(task_host):
    hub, session, brain = task_host
    hub.tasks.start(session.id, "fixture goal")
    assert brain.entered.wait(1)
    reply_token = threading.Event()
    hub._runs[session.id] = reply_token
    hub.stop_reply(session.id)
    assert reply_token.is_set()
    assert not hub.tasks._tokens[session.id].is_set()
    assert hub.tasks.invoke(session.id, "status", {})["task"]["status"] == "running"
    hub.stop_robot.assert_not_called()
    result = hub.tasks.cancel(session.id)
    assert result["ok"] and result["task"]["status"] == "cancelled"
    brain.release.set()
    hub.tasks._threads[session.id].join(1)
    assert hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"
    assert not any(m.get("text", "").startswith("Fixture") for m in hub.store.get(session.id).messages)


def test_stop_preempts_pending_model_creation(task_host, monkeypatch):
    hub, session, brain = task_host
    entered, release = threading.Event(), threading.Event()
    def load(*_):
        entered.set()
        release.wait(2)
        return brain
    monkeypatch.setattr(hub, "new_role_model", load)
    worker = threading.Thread(target=hub.tasks.start, args=(session.id, "pending"))
    worker.start()
    assert entered.wait(1)
    hub.tasks.cancel(session.id)
    release.set()
    worker.join(2)
    hub.claim_robot.assert_not_called()
    assert hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"


def test_update_versions_and_restart_never_replay(task_host):
    hub, session, brain = task_host
    hub.tasks.start(session.id, "first")
    assert brain.entered.wait(1)
    with pytest.raises(ValueError, match="version"):
        hub.tasks.update(session.id, 10, goal="second")
    hub.tasks.update(session.id, 1, goal="second")
    assert hub.tasks.snapshot(session.id)["task"]["version"] == 2
    hub.tasks.event(session.id, hub.tasks._jobs[session.id]["task_id"], 1, {"type": "reply", "text": "late"})
    assert not any(e.get("text") == "late" for e in hub.tasks.snapshot(session.id)["events"])
    from nerv.platform.robot_tasks import RobotTasks
    recovered = RobotTasks(hub)
    assert recovered.snapshot(session.id)["task"]["status"] == "interrupted"
    assert not recovered._threads


def test_production_roles_do_not_share_provider(tmp_path, monkeypatch):
    hub = Nerv(store=SessionStore(str(tmp_path)))
    created = []
    def load(name, wrap=None):
        harness = AgentHarness(SimpleNamespace(model=name, vision=True), name, ContextManager(10000, 100, 100))
        created.append(harness)
        return harness
    monkeypatch.setattr("nerv.platform.hub.brain_plugin.load", load)
    cached = hub.brain("fixture")
    agent = hub.new_role_model("fixture")
    task = hub.new_role_model("fixture")
    assert cached is not agent and agent is not task
    assert agent.llm is not task.llm


def test_cancelled_agent_cannot_launch_task(task_host):
    hub, session, brain = task_host
    hub.tasks.start(session.id, "old decision", current=lambda: False)
    hub.claim_robot.assert_not_called()
    assert hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"


def test_failed_control_claim_has_terminal_state_and_no_worker(task_host):
    hub, session, brain = task_host
    hub.claim_robot.side_effect = RuntimeError("Another controller owns execution")
    result = hub.tasks.start(session.id, "goal")
    assert not result["ok"]
    assert result["data"]["status"] == "failed"
    assert result["data"]["outcome"] == "not_started"
    assert session.id not in hub.tasks._threads


def test_unconfirmed_stop_blocks_new_task_until_confirmed(task_host):
    hub, session, brain = task_host
    hub.tasks.start(session.id, "first")
    assert brain.entered.wait(1)
    hub.stop_robot.return_value = {"ok": False, "message": "No local acknowledgement"}
    assert not hub.tasks.cancel(session.id)["ok"]
    with pytest.raises(ValueError, match="stopping"):
        hub.tasks.start(session.id, "second")
    hub.stop_robot.return_value = {"ok": True}
    assert hub.tasks.cancel(session.id)["ok"]


def test_no_late_activity_after_cancel(task_host):
    hub, session, brain = task_host
    hub.tasks.start(session.id, "goal")
    assert brain.entered.wait(1)
    hub.tasks.cancel(session.id)
    state = hub.tasks.snapshot(session.id)
    hub.tasks.event(session.id, state["task"]["task_id"], 1, {"type": "reply", "text": "late"})
    assert hub.tasks.snapshot(session.id)["cursor"] == state["cursor"]


def test_offline_dual_role_chat_queries_task_without_interrupting(tmp_path, monkeypatch):
    """Explicit protocol fixture; this does not test natural-language understanding."""
    from nerv.brain.providers.base import LLMReply, ToolCall
    from nerv.nerve.body import Capabilities, Observation
    hub = Nerv(store=SessionStore(str(tmp_path)))
    session, _ = hub.store.new("offline", "fixture-robot", None, epoch="fixture")
    session.mode = "real"  # Contract fixture only: body_client is an in-memory stub.
    hub.store.save(session)
    entered, finish = threading.Event(), threading.Event()
    requests = []
    class Provider:
        model, vision = "explicit-offline-fixture", True
        def chat(self, system, history, functions, images):
            requests.append((system, history, images))
            if "System 2 task controller" in system:
                entered.set()
                assert finish.wait(3)
                return LLMReply("Offline task fixture ended")
            if history[-1]["role"] != "tool":
                name = next(f.name for f in functions if "[robot_tasks] status:" in f.description)
                return LLMReply("Reading task status", [ToolCall("status", name, {})])
            return LLMReply("The independent task is running")
    def factory(*_, **__):
        return AgentHarness(Provider(), "offline", ContextManager(20000, 100, 100))
    monkeypatch.setattr(hub, "new_role_model", factory)
    hub._brains["offline"] = factory()
    robot = SimpleNamespace(name="fixture-robot", capabilities=lambda: Capabilities("fixture", "2"),
                            health=lambda: {"epoch": "fixture"}, post=lambda *_: {"ok": True, "running": False})
    monkeypatch.setattr(hub, "body_client", lambda _: robot)
    monkeypatch.setattr(hub, "claim_robot", Mock(return_value="owned"))
    monkeypatch.setattr(hub, "stop_robot", Mock(return_value={"ok": True}))
    monkeypatch.setattr(hub, "_control_error", lambda _: None)
    monkeypatch.setattr(hub, "_epoch_ok", lambda _: True)
    monkeypatch.setattr("nerv.platform.router.assemble", lambda *_: Observation({"frame_seq": {"camera": 1}}, []))
    try:
        hub.tasks.start(session.id, "Explicit offline task")
        assert entered.wait(1)
        response = hub.handle(session.id, "status query fixture")
        assert response["reply"] == "The independent task is running"
        assert not hub.tasks._tokens[session.id].is_set()
        hub.stop_robot.assert_not_called()
        assert len([r for r in requests if "System 2 task controller" in r[0]]) == 1
        assert all(r[2] is None for r in requests if "NERV Agent" in r[0])
        assert not any("Explicit offline task" in m.get("text", "") for m in hub.store.get(session.id).messages)
        messages = hub.store.get(session.id).messages
        calls = [c for m in messages for c in m.get("tool_calls", [])]
        assert calls[0]["domain"] == "robot_tasks" and calls[0]["capability_name"] == "status"
        assert next(m for m in messages if m["role"] == "tool")["domain"] == "robot_tasks"
        assert not any(event["type"] == "tool_call" for event in response["events"])
        hub.tasks.cancel(session.id)
    finally:
        finish.set()
        for thread in hub.tasks._threads.values():
            thread.join(3)
