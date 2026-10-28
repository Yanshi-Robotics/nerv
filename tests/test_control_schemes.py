"""Offline contract tests; no physical or model competence claims."""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nerv.platform.control_schemes import Scheme, SchemeStore
from nerv_runtime_sdk.node import BodyNode, build_app
from nerv_runtime_sdk.control import StopFlag


def test_versions_are_immutable_and_invalid_edits_rejected(tmp_path):
    store = SchemeStore(tmp_path)
    first = store.save(dict(name="Pick", robot="fixture", mode="real", template="fixed_policy", actions=["pick"]))
    second = store.save({**first.model_dump(), "name": "Next"})
    assert second.revision == 2 and store.get(first.id, 1).name == "Pick"
    with pytest.raises(ValueError, match="changed"):
        store.save(first.model_dump())
    with pytest.raises(ValueError):
        Scheme(name="Invalid", robot="fixture", mode="real", template="fixed_policy", actions=["pick", "drop"])


def node_fixture():
    impl = SimpleNamespace(name="fixture", family="fixture", version="1", held=False,
        actions=lambda: [{"name": "pick", "kind": "skill"}, {"name": "move", "kind": "primitive"},
                         {"name": "unselected_control", "kind": "control"}, {"name": "state", "kind": "read"}],
        invoke=Mock(return_value={"ok": True}), cancel_current=Mock(return_value={"ok": True}),
        execution_status=lambda: {"running": False})
    node = BodyNode(impl, guidance="", stop=StopFlag(), armed=True, require_control=True)
    return node, TestClient(build_app(node, []))


def test_grant_restricts_actions_budget_generation_and_stop():
    node, client = node_fixture()
    grant = dict(token="one", owner="host", generation=1, allowed_actions=["pick"], max_runtime_s=60, action_budget=1)
    assert client.post("/control/claim", json=grant).json()["ok"]
    with pytest.raises(InterruptedError, match="outside"):
        node.invoke("move", _nerv_control="one")
    with pytest.raises(InterruptedError, match="outside"):
        node.invoke("unselected_control", _nerv_control="one")
    assert node.invoke("pick", _nerv_control="one")["ok"]
    with pytest.raises(InterruptedError, match="budget"):
        node.invoke("pick", _nerv_control="one")
    assert node.invoke("state")["ok"]
    assert not client.post("/control/claim", json=grant).json()["ok"]
    assert client.post("/stop", json={}).json()["ok"]
    with pytest.raises(InterruptedError):
        node.invoke("pick", _nerv_control="one")


def test_missing_grant_and_expired_grant_fail_closed():
    from threading import Event
    node, client = node_fixture()
    cancelled = Event()
    node.impl.cancel_current.side_effect = lambda reason: (cancelled.set() or {"ok": True})
    assert not client.post("/control/claim", json={"token": "legacy", "owner": "host", "generation": 1}).json()["ok"]
    assert client.post("/control/claim", json=dict(token="one", owner="host", generation=1, allowed_actions=["move"], max_runtime_s=.02, action_budget=1)).json()["ok"]
    # Observe the expiry callback, not an assumed scheduler latency on CI hosts.
    assert cancelled.wait(2), "Expired control grant did not cancel execution"
    with pytest.raises(InterruptedError):
        node.invoke("move", _nerv_control="one")
    node.impl.cancel_current.assert_called_with("control_grant_expired")


@pytest.mark.parametrize("stop_confirmed", [True, False])
def test_fixed_policy_does_not_create_model_and_outcome_stays_unknown(tmp_path, monkeypatch, stop_confirmed):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    from nerv.nerve.body import Capabilities, ToolSpec, Observation, ActionResult
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    hub.schemes = SchemeStore(tmp_path / "schemes")
    session, _ = hub.store.new("operator", "fixture", None, epoch="epoch")
    session.mode, session.armed = "real", True
    hub.store.save(session)
    robot = SimpleNamespace(name="fixture", capabilities=lambda: Capabilities("fixture", "3", robot_actions=[
        ToolSpec("pick", "Offline fixture skill", {"type": "object", "properties": {}}, "skill")]),
        health=lambda: {"epoch": "epoch", "supervision_sensors": []},
        perceive=lambda: Observation({"frame_seq": 1}, []),
        invoke=Mock(return_value=ActionResult(True, "Started", {"execution_id": "execution"})),
        post=Mock(return_value={"running": False, "state": "finished", "execution_id": "execution"}))
    monkeypatch.setattr(hub, "body_client", lambda _: robot)
    monkeypatch.setattr(hub, "_control_error", lambda _: None)
    monkeypatch.setattr(hub, "claim_robot", Mock(return_value="token"))
    monkeypatch.setattr(hub, "stop_robot", Mock(return_value={"ok": stop_confirmed}))
    monkeypatch.setattr(hub, "new_role_model", Mock(side_effect=AssertionError("No model allowed")))
    scheme = hub.schemes.save(dict(name="Fixed", robot="fixture", mode="real", template="fixed_policy", actions=["pick"]))
    hub.tasks.select_scheme(session.id, scheme.id)
    hub.tasks.start(session.id, "Pick")
    hub.tasks._threads[session.id].join(2)
    state = hub.tasks.snapshot(session.id)["task"]
    assert state["status"] == ("finished" if stop_confirmed else "failed"), state
    assert state["outcome"] == "unknown" and state["model"] is None
    robot.invoke.assert_called_once()
    hub.new_role_model.assert_not_called()
    hub.claim_robot.assert_called_once_with(session.id, allowed_actions=["pick"], max_runtime_s=120.0, action_budget=1)
    if not stop_confirmed:
        with pytest.raises(ValueError, match="stopping"):
            hub.tasks.start(session.id, "Retry")
        with pytest.raises(ValueError, match="Stop the task"):
            hub.tasks.select_scheme(session.id, scheme.id)


def test_agent_cannot_enumerate_or_invoke_motion(tmp_path, monkeypatch):
    import threading
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    from nerv.platform.role_turn import RoleTurn
    from nerv.nerve.body import Capabilities, ToolSpec
    from nerv.nerve.brain import CallTool
    hub = Nerv(store=SessionStore(str(tmp_path)))
    session, _ = hub.store.new("fixture", "fixture", None)
    session.mode = "real"
    robot = SimpleNamespace(capabilities=lambda: Capabilities("fixture", "3", robot_actions=[
        ToolSpec("move", "move", {"type": "object"}, "primitive"),
        ToolSpec("read", "read", {"type": "object"}, "read")]))
    monkeypatch.setattr(hub, "body_client", lambda _: robot)
    turn = RoleTurn(hub, session, None, lambda _: None, threading.Event())
    turn.tools()
    assert not any(cap.name == "move" for cap in turn.catalog._entries.values())
    assert not turn.call_tool(CallTool("1", "move", {})).ok


def test_stop_does_not_deadlock_dispatch_and_rejects_new_claim():
    import threading
    node, client = node_fixture()
    grant = dict(token="one", owner="host", generation=1, allowed_actions=["pick"], max_runtime_s=60, action_budget=1)
    assert client.post('/control/claim', json=grant).json()['ok']
    entered, finish = threading.Event(), threading.Event()
    def cancel(_reason):
        entered.set()
        # A cancelled worker must be able to acquire the dispatch guard.
        with pytest.raises(InterruptedError):
            with node.control_guard('one'):
                pass
        finish.wait(1)
        return {'ok': True}
    node.impl.cancel_current = cancel
    thread = threading.Thread(target=lambda: client.post('/stop', json={}))
    thread.start()
    assert entered.wait(1)
    assert not client.post('/control/claim', json={**grant, 'token': 'two', 'generation': 2}).json()['ok']
    finish.set()
    thread.join(2)
    assert not thread.is_alive()
    assert client.post('/control/claim', json={**grant, 'token': 'two', 'generation': 2}).json()['ok']


def test_host_restart_never_restores_motion_authority(tmp_path):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    store = SessionStore(str(tmp_path))
    session, _ = store.new('operator', 'fixture', None)
    store.set_armed(session.id, True)
    Nerv(store=store)
    recovered = store.get(session.id)
    assert recovered.armed is False
    assert recovered.status == 'reconnect_required'
