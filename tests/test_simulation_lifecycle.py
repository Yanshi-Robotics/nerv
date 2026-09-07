"""Stopping a local simulation preserves history and isolates the next body binding."""
from unittest.mock import Mock
import threading

import pytest

from nerv.nerve import operator as op
from nerv.platform.hub import Nerv
from nerv.platform.launcher import NodeHandle
from nerv.platform.session import SessionStore


@pytest.fixture
def simulation(tmp_path, monkeypatch):
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    body = "humanoid-unitree-g1"
    world = "apt"
    events = []
    for key, kind in [(f"body:{body}", "body"), (f"world:{world}/{body}", "world")]:
        proc = Mock()
        proc.poll.return_value = None
        proc.terminate.side_effect = lambda key=key: events.append(key)
        hub.launcher.nodes[key] = NodeHandle(kind, key, f"http://{kind}.invalid", proc=proc,
                                             meta={"world": world, "epoch": "body-instance"})
    session, _ = hub.store.new("mock", body, world, tools=[], epoch="original")
    hub.store.append(session.id, {"role": "user", "text": "retain this history"})
    hub.store.set_armed(session.id, True)
    bc, wc = Mock(), Mock()
    wc.epoch.return_value = "original"
    monkeypatch.setattr(hub, "body_client", Mock(return_value=bc))
    monkeypatch.setattr(hub, "world_client", Mock(return_value=wc))
    return hub, hub.store.get(session.id), bc, wc, events


def test_stop_pair_preserves_history_and_other_nodes(simulation):
    hub, session, bc, wc, events = simulation
    other, _ = hub.store.new("mock", None, None, tools=[])
    hub.launcher.nodes["tool:calculator"] = NodeHandle("tool", "tool:calculator", "http://unused", attached=True)
    hub._bodies[session.body] = bc
    hub._worlds[f"{session.world}/{session.body}"] = wc
    result = hub.stop_simulation(f"body:{session.body}", session.world, "body-instance")
    assert result["ok"]
    assert events == [f"body:{session.body}", f"world:{session.world}/{session.body}"]
    saved = hub.store.get(session.id)
    assert saved.status == op.SESSION_FROZEN and not saved.armed
    assert saved.messages == session.messages and saved.epoch == session.epoch
    assert hub.store.get(other.id) == other
    assert list(hub.launcher.nodes) == ["tool:calculator"]
    assert not hub._bodies and not hub._worlds


@pytest.mark.parametrize("kind", ["body", "world"])
def test_attached_pair_refused_without_side_effects(simulation, kind):
    hub, session, _, _, events = simulation
    key = f"body:{session.body}" if kind == "body" else f"world:{session.world}/{session.body}"
    hub.launcher.nodes[key].attached = True
    with pytest.raises(ValueError, match="externally managed"):
        hub.stop_simulation(f"body:{session.body}", session.world, "body-instance")
    assert not events and hub.store.get(session.id) == session


@pytest.mark.parametrize("world,epoch", [("house", "body-instance"), ("apt", "old-instance")])
def test_old_confirmation_cannot_stop_replacement(simulation, world, epoch):
    hub, session, _, _, events = simulation
    with pytest.raises(ValueError, match="simulation changed"):
        hub.stop_simulation(f"body:{session.body}", world, epoch)
    assert not events and hub.store.get(session.id) == session


@pytest.mark.parametrize("state", [op.SESSION_FROZEN, op.SESSION_RECONNECT, "wrong_world", "wrong_epoch"])
@pytest.mark.parametrize("action", ["arm", "stop", "estop", "release", "reset_world", "perceive"])
def test_stale_session_cannot_touch_current_body(simulation, state, action):
    hub, session, bc, wc, _ = simulation
    if state == "wrong_world":
        hub.launcher.nodes[f"body:{session.body}"].meta["world"] = "house"
    elif state == "wrong_epoch":
        wc.epoch.return_value = "restarted"
    else:
        hub.store.set_status(session.id, state)
    result = hub.arm(session.id, True) if action == "arm" else getattr(hub, action)(session.id)
    if isinstance(result, dict):
        assert not result["ok"]
    bc.assert_not_called()
    assert not bc.mock_calls
    wc.reset.assert_not_called()
    wc.perception.assert_not_called()
    assert hub.store.get(session.id).messages == session.messages


def test_offline_body_cannot_be_armed(simulation):
    hub, session, _, _, _ = simulation
    hub.body_client.return_value = None
    hub.store.set_armed(session.id, False)
    assert not hub.arm(session.id, True)["ok"]
    assert not hub.store.get(session.id).armed


def test_emergency_stop_does_not_wait_for_camera_io(simulation, monkeypatch):
    hub, session, bc, _, _ = simulation
    entered, release, held = threading.Event(), threading.Event(), threading.Event()
    def observation(*args):
        entered.set()
        assert release.wait(timeout=2)
        return object()
    def hold(reason):
        held.set()
        return {"ok": True, "held": True}
    monkeypatch.setattr("nerv.platform.observe.assemble", observation)
    bc.hold.side_effect = hold
    reader = threading.Thread(target=hub.perceive, args=(session.id,))
    stopper = threading.Thread(target=hub.estop, args=(session.id,))
    try:
        reader.start()
        assert entered.wait(timeout=2)
        stopper.start()
        assert held.wait(timeout=1), "emergency control waited for a blocked observation"
    finally:
        release.set()
        reader.join(timeout=2)
        stopper.join(timeout=2)


def test_observation_finished_after_freeze_is_discarded(simulation, monkeypatch):
    hub, session, _, _, _ = simulation
    def observation(*args):
        hub.store.set_status(session.id, op.SESSION_FROZEN)
        return object()
    monkeypatch.setattr("nerv.platform.observe.assemble", observation)
    assert hub.perceive(session.id) is None
