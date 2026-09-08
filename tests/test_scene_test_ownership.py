"""Scene tests cannot share motion control or outlive their owning session."""
import threading

import pytest

from nerv.nerve import operator as op
from test_simulation_lifecycle import simulation as simulation_fixture

simulation = simulation_fixture


def acquire(simulation):
    hub, session, body, world, _ = simulation
    body.status.return_value = {"running_skill": "", "held": False}
    body.set_config.return_value = {"ok": True}
    def post(operation, payload):
        return {"ok": True, "owner": payload.get("owner"), "token": "lease-token",
                "epoch": session.epoch, "lease_seconds": 2}
    world.scene_post.side_effect = post
    result = hub.scene_tests.acquire(session.id, "tab")
    return hub, session, body, world, {k: result[k] for k in ("owner", "token", "epoch")}


def test_acquire_disarms_and_blocks_arm_and_teleop(simulation):
    hub, session, body, _, credentials = acquire(simulation)
    assert not hub.store.get(session.id).armed
    body.post.assert_called_with("/stop")
    assert not hub.arm(session.id, True)["ok"]
    result = list(hub.teleop_stream(session.id, "anything", {}))
    assert result[0]["ok"] is False
    hub.scene_tests.action(session.id, "release", credentials)
    assert not hub.scene_tests.blocks(session.body)
    assert hub.arm(session.id, True)["ok"]


@pytest.mark.parametrize("key,value", [("owner", "other"), ("token", "bad"), ("epoch", "old")])
def test_foreign_credentials_cannot_operate(simulation, key, value):
    hub, session, _, world, credentials = acquire(simulation)
    world.scene_post.reset_mock()
    credentials[key] = value
    with pytest.raises(ValueError):
        hub.scene_tests.action(session.id, "command", {**credentials, "action": "anything"})
    world.scene_post.assert_not_called()


def test_frozen_session_cannot_renew_lease(simulation):
    hub, session, _, world, credentials = acquire(simulation)
    world.scene_post.reset_mock()
    hub.store.set_status(session.id, op.SESSION_FROZEN)
    with pytest.raises(ValueError):
        hub.scene_tests.action(session.id, "heartbeat", credentials)
    world.scene_post.assert_not_called()


def test_slow_scene_cancellation_does_not_delay_estop(simulation):
    hub, session, body, world, _ = acquire(simulation)
    blocked, release = threading.Event(), threading.Event()
    def slow(operation, payload):
        blocked.set()
        release.wait(timeout=3)
        return {"ok": True}
    world.scene_post.side_effect = slow
    try:
        hub.estop(session.id)
        body.hold.assert_called_once_with("operator")
        assert blocked.wait(timeout=1)
    finally:
        release.set()
