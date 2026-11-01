"""Offline external-agent security and lifecycle fixtures, never hardware evidence."""
import base64
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema.exceptions import ValidationError

from nerv.nerve.body import ActionResult, Capabilities, Observation, ToolSpec
from nerv.platform.agent_gateway import AgentGateway
from nerv.platform.control_schemes import SchemeStore
from nerv.platform.hub import Nerv
from nerv.platform.session import SessionStore


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    hub.schemes = SchemeStore(tmp_path / "schemes")
    session, _ = hub.store.new("operator", "fixture", "fixture-world", epoch="epoch")
    session.armed = True
    hub.store.save(session)
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lfoAAAAASUVORK5CYII=")
    robot = SimpleNamespace(name="fixture", capabilities=lambda: Capabilities("fixture", "3", robot_actions=[
        ToolSpec("move", "Move", {"type": "object", "properties": {}, "additionalProperties": False}, "primitive"),
        ToolSpec("other", "Other", {"type": "object"}, "primitive")], sensors=["camera"],
        config={"external_supervision": {"validated": True, "timeout_s": 30, "max_observation_age_s": 2}}),
        health=lambda: {"epoch": "epoch", "supervision_sensors": ["camera"]},
        perceive=lambda: Observation({"frame_seq": 1}, [{"name": "camera", "png": png}]),
        invoke=Mock(return_value=ActionResult(True, "Completed")),
        post=Mock(return_value={"ok": True, "running": False}))
    monkeypatch.setattr(hub, "body_client", lambda _: robot)
    monkeypatch.setattr(hub, "world_client", lambda *_: None)
    monkeypatch.setattr(hub, "_control_error", lambda _: None)
    monkeypatch.setattr(hub, "claim_robot", Mock(return_value="token"))
    monkeypatch.setattr(hub, "stop_robot", Mock(return_value={"ok": True}))
    monkeypatch.setattr(hub, "new_role_model", Mock(side_effect=AssertionError("No model allowed")))
    scheme = hub.schemes.save(dict(name="External", robot="fixture", mode="sim", template="external_vlm", actions=["move"]))
    hub.tasks.select_scheme(session.id, scheme.id)
    projects = SimpleNamespace(get=lambda pid: {"id": pid}, active_run=lambda pid: {"id": "run", "session_id": session.id},
                               require_session=lambda pid: session, create_run=Mock(return_value={"run": {"id": "run"}}),
                               stop_run=Mock(return_value={"ok": True}))
    gw = AgentGateway(hub, projects, tmp_path / "agent-connections")
    paired = gw.create("Test Agent", ["project"], ["observe", "control"])
    gw.set_control("project", accepting=True, owner_id=paired["connection"]["id"])
    yield gw, paired, hub, session, robot
    gw.shutdown()
    for token in hub.tasks._tokens.values():
        token.set()
    for thread in hub.tasks._threads.values():
        thread.join(1)


def acknowledge(gw, paired):
    observation = gw.invoke(paired["token"], "observe", {"project_id": "project"})
    result = gw.invoke(paired["token"], "acknowledge", {"project_id": "project", "receipt_id": observation["receipt_id"]})
    assert result["ok"]
    return observation


def start(gw, paired):
    acknowledge(gw, paired)
    return gw.invoke(paired["token"], "task_start", {"project_id": "project", "request_id": "start", "goal": "Move"})["data"]


def test_pairing_private_scoped_and_revocable(gateway):
    gw, paired, hub, session, _ = gateway
    assert "token_hash" not in str(gw.list())
    assert paired["token"] not in str(gw.list())
    assert (gw._path(paired["connection"]["id"]).stat().st_mode & 0o777) == 0o600
    with pytest.raises(PermissionError):
        gw.invoke(paired["token"] + "wrong", "projects_list", {})
    with pytest.raises(PermissionError):
        gw.invoke(paired["token"], "project_get", {"project_id": "outside"})
    gw.revoke(paired["connection"]["id"])
    with pytest.raises(PermissionError):
        gw.invoke(paired["token"], "projects_list", {})
    hub.new_role_model.assert_not_called()


def test_observation_does_not_acknowledge_or_arm(gateway):
    gw, paired, hub, session, robot = gateway
    robot.post.reset_mock()
    obs = gw.invoke(paired["token"], "observe", {"project_id": "project"})
    assert obs["images"][0]["mimeType"] == "image/png"
    robot.post.assert_not_called()
    assert gw.control("project")["supervision"] is None
    with pytest.raises(ValueError, match="acknowledge"):
        gw.invoke(paired["token"], "task_start", {"project_id": "project", "request_id": "s", "goal": "Move"})
    hub.claim_robot.assert_not_called()


def test_receipts_one_use_and_instance_bound(gateway):
    gw, paired, _, session, _ = gateway
    obs = acknowledge(gw, paired)
    with pytest.raises(ValueError, match="consumed"):
        gw.invoke(paired["token"], "acknowledge", {"project_id": "project", "receipt_id": obs["receipt_id"]})
    obs = gw.invoke(paired["token"], "observe", {"project_id": "project"})
    session.epoch = "new"
    with pytest.raises(ValueError, match="instance"):
        gw.invoke(paired["token"], "acknowledge", {"project_id": "project", "receipt_id": obs["receipt_id"]})


def test_actual_external_task_uses_gate_allowlist_and_no_model(gateway):
    gw, paired, hub, session, robot = gateway
    task = start(gw, paired)
    assert task["status"] == "running" and task["model"] is None
    args = {"project_id": "project", "request_id": "move", "task_id": task["task_id"], "version": 1,
            "action": "move", "arguments": {}}
    result = gw.invoke(paired["token"], "action", args)
    assert result["ok"]
    assert gw.invoke(paired["token"], "action", args) == result
    robot.invoke.assert_called_once()
    with pytest.raises(ValueError, match="again"):
        gw.invoke(paired["token"], "action", {**args, "request_id": "next"})
    acknowledge(gw, paired)
    with pytest.raises(ValueError, match="outside"):
        gw.invoke(paired["token"], "action", {**args, "request_id": "other", "action": "other"})
    acknowledge(gw, paired)
    fresh = hub.store.get(session.id)
    fresh.armed = False
    hub.store.save(fresh)
    assert not gw.invoke(paired["token"], "action", {**args, "request_id": "disarmed"})["ok"]
    robot.invoke.assert_called_once()
    hub.new_role_model.assert_not_called()


def test_unknown_outcome_is_not_replayed(gateway, monkeypatch):
    gw, paired, _, _, _ = gateway
    fn = Mock(side_effect=RuntimeError("transport lost"))
    monkeypatch.setattr(gw.projects, "create_run", fn)
    args = {"project_id": "project", "request_id": "start"}
    with pytest.raises(RuntimeError):
        gw.invoke(paired["token"], "run_start", args)
    with pytest.raises(ValueError, match="unknown"):
        gw.invoke(paired["token"], "run_start", args)
    fn.assert_called_once()
    with pytest.raises(ValueError, match="different"):
        gw.invoke(paired["token"], "activity", {**args, "text": "changed"})


def test_expiry_cancels_matching_task_and_revokes_commands(gateway):
    gw, paired, hub, session, _ = gateway
    start(gw, paired)
    gw._leases["project"]["deadline"] = time.monotonic() - 1
    gw.check_expired()
    assert not gw.control("project")["accepting"]
    assert hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"


def test_takeover_requires_confirmed_stop(gateway):
    gw, paired, hub, session, _ = gateway
    start(gw, paired)
    hub.stop_robot.return_value = {"ok": False, "message": "Disconnected"}
    result = gw.set_control("project", accepting=False)
    assert not result["ok"] and not result["accepting"] and result["owner_id"] is None
    assert hub.tasks.snapshot(session.id)["task"]["outcome"] == "unknown"


def test_observe_permission_cannot_control_or_forge_source(gateway):
    gw, _, hub, session, _ = gateway
    paired = gw.create("Observer", ["project"], ["observe"])
    with pytest.raises(PermissionError):
        gw.invoke(paired["token"], "run_start", {"project_id": "project", "request_id": "r"})
    with pytest.raises(ValidationError):
        gw.invoke(paired["token"], "activity", {"project_id": "project", "request_id": "a", "text": "Done", "source": "runtime"})
    gw.invoke(paired["token"], "activity", {"project_id": "project", "request_id": "b", "text": "Agent claim"})
    hub.store.memory(session.id).append("robot_activity", {"type": "thinking", "text": "private reasoning"})
    events = gw.invoke(paired["token"], "events", {"project_id": "project", "limit": 1})
    assert events["events"][0]["source"] == "external_agent"
    assert "private reasoning" not in str(events)


def test_pause_does_not_claim_task_stopped_and_stop_still_works(gateway):
    gw, paired, hub, session, _ = gateway
    start(gw, paired)
    gw.pause("project")
    assert hub.tasks.snapshot(session.id)["task"]["status"] == "running"
    assert gw.invoke(paired["token"], "task_cancel", {"project_id": "project", "request_id": "stop"})["ok"]


def test_contract_unknown_stays_observation_only(gateway):
    gw, paired, hub, _, robot = gateway
    robot.capabilities = lambda: Capabilities("fixture", "3")
    observation = gw.invoke(paired["token"], "observe", {"project_id": "project"})
    with pytest.raises(ValueError, match="validated"):
        gw.invoke(paired["token"], "acknowledge", {"project_id": "project", "receipt_id": observation["receipt_id"]})
    hub.claim_robot.assert_not_called()


def test_restart_preserves_pairing_not_control(gateway):
    gw, paired, hub, _, _ = gateway
    other = AgentGateway(hub, gw.projects, gw.root)
    try:
        assert other.invoke(paired["token"], "projects_list", {})["projects"]
        assert not other.control("project")["accepting"]
    finally:
        other.shutdown()


def test_parallel_duplicate_request_never_spawns_twice(gateway, monkeypatch):
    gw, paired, _, _, _ = gateway
    entered, finish = threading.Event(), threading.Event()
    def create(*args, **kwargs):
        entered.set()
        finish.wait(2)
        return {"ok": True}
    fn = Mock(side_effect=create)
    monkeypatch.setattr(gw.projects, "create_run", fn)
    args = {"project_id": "project", "request_id": "parallel"}
    worker = threading.Thread(target=gw.invoke, args=(paired["token"], "run_start", args))
    worker.start()
    assert entered.wait(1)
    with pytest.raises(ValueError, match="unknown"):
        gw.invoke(paired["token"], "run_start", args)
    finish.set()
    worker.join(3)
    fn.assert_called_once()


def test_operator_heartbeat_cannot_mask_external_task(gateway):
    gw, paired, hub, session, _ = gateway
    task = start(gw, paired)
    assert task["supervisor"]["kind"] == "external_agent"
    with pytest.raises(ValueError, match="Agent observations"):
        hub.tasks.allow_operator_supervision(session.id)
    activity = hub.tasks.snapshot(session.id)["events"]
    assert any(e.get("type") == "command_request" and e.get("text") == "Move" for e in activity)


def test_stop_during_external_capability_loading_does_not_resurrect_task(gateway, monkeypatch):
    gw, paired, hub, session, robot = gateway
    acknowledge(gw, paired)
    entered, finish = threading.Event(), threading.Event()
    original = robot.capabilities
    calls = []
    def delayed():
        calls.append(1)
        if len(calls) == 2:  # Resolve first; launch tools second.
            entered.set()
            finish.wait(2)
        return original()
    monkeypatch.setattr(robot, "capabilities", delayed)
    result = []
    worker = threading.Thread(target=lambda: result.append(gw.invoke(paired["token"], "task_start", {
        "project_id": "project", "request_id": "pending", "goal": "Wait"})))
    worker.start()
    assert entered.wait(1)
    hub.tasks.cancel(session.id)
    finish.set()
    worker.join(2)
    assert hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"
    assert session.id not in hub.tasks._external_turns


def test_control_and_receipts_do_not_survive_run_replacement(gateway):
    gw, paired, hub, session, _ = gateway
    acknowledge(gw, paired)
    assert gw.control("project")["supervision_state"] == "confirmed"
    # Deliberately keep the same session and Runtime epoch: Run identity is an
    # independent authorization boundary, not inferred from either field.
    gw.projects.active_run = lambda pid: {"id": "replacement", "session_id": session.id}
    status = gw.control("project")
    assert not status["accepting"] and status["owner_id"] is None
    assert status["supervision"] is None and status["last_observation_at"] is None
    assert status["last_acknowledged_at"] is None and status["supervision_state"] == "not_confirmed"
    with pytest.raises(PermissionError):
        gw.invoke(paired["token"], "task_start", {"project_id": "project", "request_id": "new-run-task", "goal": "Move"})
    gw.set_control("project", accepting=True, owner_id=paired["connection"]["id"])
    with pytest.raises(ValueError, match="fresh frame"):
        gw.invoke(paired["token"], "task_start", {"project_id": "project", "request_id": "new-owner-task", "goal": "Move"})
    hub.claim_robot.assert_not_called()


def test_motion_checks_run_identity_without_a_prior_status_read(gateway):
    gw, paired, _, session, _ = gateway
    observation = acknowledge(gw, paired)
    gw.projects.active_run = lambda pid: {"id": "replacement", "session_id": session.id}
    with pytest.raises(PermissionError):
        gw.invoke(paired["token"], "acknowledge", {"project_id": "project", "receipt_id": observation["receipt_id"]})
    assert not gw._leases and not gw._receipts


def test_control_assignment_requires_a_run_and_rechecks_after_stop(gateway):
    gw, paired, hub, session, _ = gateway
    gw.projects.active_run = lambda pid: None
    with pytest.raises(ValueError, match="Start a Run"):
        gw.set_control("project", accepting=True, owner_id=paired["connection"]["id"])
    gw.projects.active_run = lambda pid: {"id": "before", "session_id": session.id}
    def changed(*args, **kwargs):
        gw.projects.active_run = lambda pid: {"id": "after", "session_id": session.id}
        return {"ok": True}
    hub.stop_robot.side_effect = changed
    with pytest.raises(ValueError, match="Run changed"):
        gw.set_control("project", accepting=True, owner_id=paired["connection"]["id"])
    assert not gw.control("project")["accepting"]


def test_revoking_stale_owner_does_not_stop_a_replacement_run(gateway):
    gw, paired, hub, session, _ = gateway
    hub.stop_robot.reset_mock()
    gw.projects.active_run = lambda pid: {"id": "replacement", "session_id": session.id}
    assert gw.revoke(paired["connection"]["id"])["ok"]
    hub.stop_robot.assert_not_called()
