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
    returned = threading.Event()
    def slow(operation, payload):
        blocked.set()
        release.wait(timeout=3)
        return {"ok": True}
    world.scene_post.side_effect = slow
    def estop():
        hub.estop(session.id)
        returned.set()
    worker = threading.Thread(target=estop)
    worker.start()
    try:
        assert blocked.wait(timeout=1)
        assert returned.wait(timeout=1), "E-stop waited for the blocked scene release"
        assert not release.is_set()
        body.hold.assert_called_once_with("operator")
    finally:
        release.set()
        worker.join(timeout=3)


@pytest.mark.parametrize("operation,ok", [("heartbeat", True), ("heartbeat", False), ("release", True)])
def test_delayed_old_response_cannot_modify_later_owner(simulation, operation, ok):
    hub, session, _, world, credentials = acquire(simulation)
    arrived, finish = threading.Event(), threading.Event()
    failures = []

    def delayed(_operation, payload):
        if payload["owner"] == credentials["owner"]:
            arrived.set()
            assert finish.wait(timeout=3)
            return {"ok": ok, "lease_seconds": 99}
        return {"ok": True, "token": "later-token", "owner": payload["owner"],
                "epoch": session.epoch, "lease_seconds": 2}

    def request():
        try:
            hub.scene_tests.action(session.id, operation, credentials)
        except Exception as exc:
            failures.append(exc)

    world.scene_post.side_effect = delayed
    worker = threading.Thread(target=request)
    worker.start()
    try:
        assert arrived.wait(timeout=1)
        hub.scene_tests.cancel(session.id, release=False)
        hub.scene_tests.acquire(session.id, "later-tab")
        later = hub.scene_tests._owners[session.id]
        deadline = later["expires"]
        finish.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert not failures
        assert hub.scene_tests._owners[session.id] is later
        assert later["expires"] == deadline
    finally:
        finish.set()
        worker.join(timeout=3)


def test_interrupt_detaches_before_delayed_release_thread(simulation, monkeypatch):
    hub, session, _, _, _ = acquire(simulation)
    from nerv.platform import scene_tests
    queued = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            queued.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(scene_tests.threading, "Thread", DeferredThread)
    hub.scene_tests.interrupt(session.id)
    assert session.id not in hub.scene_tests._owners
    hub.scene_tests.acquire(session.id, "later-tab")
    later = hub.scene_tests._owners[session.id]
    for target, args in queued:
        target(*args)
    assert hub.scene_tests._owners[session.id] is later
