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
        return {
            "ok": True,
            "owner": payload.get("owner"),
            "token": "lease-token",
            "epoch": session.epoch,
            "lease_seconds": 2,
        }

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


@pytest.mark.parametrize(
    "operation,ok", [("heartbeat", True), ("heartbeat", False), ("release", True)]
)
def test_delayed_old_response_cannot_modify_later_owner(simulation, operation, ok):
    hub, session, _, world, credentials = acquire(simulation)
    arrived, finish = threading.Event(), threading.Event()
    failures = []

    def delayed(_operation, payload):
        if payload["owner"] == credentials["owner"]:
            arrived.set()
            assert finish.wait(timeout=3)
            return {"ok": ok, "lease_seconds": 99}
        return {
            "ok": True,
            "token": "later-token",
            "owner": payload["owner"],
            "epoch": session.epoch,
            "lease_seconds": 2,
        }

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


def test_cancelled_acquisition_does_not_disarm_a_resumed_session(simulation):
    hub, session, body, world, _ = simulation
    arrived, continue_status = threading.Event(), threading.Event()
    errors = []

    def status():
        arrived.set()
        assert continue_status.wait(2)
        return {"running_skill": "", "held": False}

    def scene_post(operation, payload):
        return {
            "ok": True,
            "owner": payload["owner"],
            "token": "lease",
            "epoch": session.epoch,
            "lease_seconds": 2,
        }

    body.status.side_effect = status
    body.set_config.return_value = {"ok": True}
    world.scene_post.side_effect = scene_post

    def acquire():
        try:
            hub.scene_tests.acquire(session.id, "cancelled-tab")
        except ValueError as error:
            errors.append(str(error))

    worker = threading.Thread(target=acquire)
    worker.start()
    try:
        assert arrived.wait(2)
        hub.scene_tests.interrupt(session.id)
        assert session.id not in hub.scene_tests._owners
        assert hub.arm(session.id, True)["ok"]
        body.set_config.reset_mock()
        continue_status.set()
        worker.join(2)
        assert not worker.is_alive()
        assert errors == ["Scene acquisition was interrupted"]
        assert hub.store.get(session.id).armed, (
            "An already-cancelled acquire disarmed the resumed session"
        )
        body.set_config.assert_not_called()
    finally:
        continue_status.set()
        worker.join(2)


def test_cancel_during_disarm_io_orders_rearm_without_blocking_estop(simulation):
    """A cancelled entry must not finish its old disarm after the next user enable."""
    hub, session, body, world, _ = simulation
    disarm_entered = threading.Event()
    finish_disarm = threading.Event()
    arm_requested = threading.Event()
    arm_applied = threading.Event()
    held = threading.Event()
    remote_states = []
    acquisition_errors = []
    thread_errors = []
    results = {}

    body.status.return_value = {"running_skill": "", "held": False}
    world.scene_post.side_effect = lambda operation, payload: {
        "ok": True,
        "owner": payload["owner"],
        "token": "lease",
        "epoch": session.epoch,
        "lease_seconds": 2,
    }

    def config(key, value):
        assert key == "armed"
        if value == "false":
            disarm_entered.set()
            assert finish_disarm.wait(2), "test did not release the blocked config IO"
        remote_states.append(value)
        if value == "true":
            arm_applied.set()
        return {"ok": True}

    def hold(reason):
        held.set()
        return {"ok": True, "held": True}

    body.set_config.side_effect = config
    body.hold.side_effect = hold

    def acquire():
        try:
            hub.scene_tests.acquire(session.id, "cancelled-config-tab")
        except ValueError as error:
            acquisition_errors.append(str(error))
        except BaseException as error:
            thread_errors.append(error)

    def rearm():
        try:
            arm_requested.set()
            results["arm"] = hub.arm(session.id, True)
        except BaseException as error:
            thread_errors.append(error)

    def estop():
        try:
            results["estop"] = hub.estop(session.id)
        except BaseException as error:
            thread_errors.append(error)

    entry = threading.Thread(target=acquire)
    resume = threading.Thread(target=rearm)
    stop = threading.Thread(target=estop)
    entry.start()
    try:
        assert disarm_entered.wait(2)
        stop.start()
        assert held.wait(0.5), "emergency stop waited for scene-entry config IO"
        stop.join(0.5)
        assert not stop.is_alive()
        assert results["estop"]["ok"]
        assert session.id not in hub.scene_tests._owners
        resume.start()
        assert arm_requested.wait(0.5)
        applied_before_old_config_finished = arm_applied.wait(0.1)
        finish_disarm.set()
        entry.join(2)
        resume.join(2)
        assert not entry.is_alive() and not resume.is_alive()
        assert not thread_errors
        assert acquisition_errors == ["Scene acquisition was interrupted"]
        assert not applied_before_old_config_finished, "new enable overtook an in-flight old disarm"
        assert remote_states == ["false", "true"]
        assert results["arm"]["ok"] and hub.store.get(session.id).armed
        assert not world.scene_post.mock_calls, "cancelled entry still acquired the world lease"
    finally:
        finish_disarm.set()
        for thread in (entry, resume, stop):
            if thread.ident is not None:
                thread.join(2)
