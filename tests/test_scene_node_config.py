"""Generic body configuration must obey the same scene-test ownership as arming."""

import threading

import pytest
from fastapi import HTTPException

from test_scene_test_ownership import acquire
from test_simulation_lifecycle import simulation as simulation_fixture

simulation = simulation_fixture


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "armed", " true ", "ARMED"])
def test_node_config_enable_aliases_cannot_bypass_scene_ownership(simulation, monkeypatch, value):
    from nerv.platform import server

    hub, session, body, _, _ = acquire(simulation)
    monkeypatch.setattr(server, "nerv", hub)
    body.set_config.reset_mock()
    with pytest.raises(HTTPException) as error:
        server.node_config(f"body:{session.body}", server.ConfigIn(key="armed", value=value))
    assert error.value.status_code == 409
    body.set_config.assert_not_called()
    assert hub.scene_tests.blocks(session.body)


def test_node_config_rechecks_ownership_after_waiting_for_body_lock(simulation, monkeypatch):
    from nerv.platform import server

    hub, session, body, _, _ = simulation
    monkeypatch.setattr(server, "nerv", hub)
    started, returned = threading.Event(), threading.Event()
    outcomes = []

    def configure():
        started.set()
        try:
            outcomes.append(server.node_config(
                f"body:{session.body}", server.ConfigIn(key="armed", value="true")))
        except HTTPException as error:
            outcomes.append(error.status_code)
        finally:
            returned.set()

    worker = threading.Thread(target=configure)
    try:
        with hub.body_control_lock(session.body):
            worker.start()
            assert started.wait(1)
            assert not returned.wait(0.1), "configuration ignored the body control lock"
            acquire(simulation)
            body.set_config.reset_mock()
        worker.join(2)
        assert not worker.is_alive()
        assert outcomes == [409]
        body.set_config.assert_not_called()
    finally:
        if worker.ident is not None:
            worker.join(2)


def test_inflight_node_config_finishes_before_scene_entry_disarms(simulation, monkeypatch):
    from nerv.platform import server

    hub, session, body, world, _ = simulation
    monkeypatch.setattr(server, "nerv", hub)
    config_entered, finish_config, entry_waiting, world_leased = (threading.Event() for _ in range(4))
    states, failures = [], []
    body.status.return_value = {"running_skill": "", "held": False}

    def scene_post(operation, payload):
        world_leased.set()
        return {"ok": True, "owner": payload["owner"], "token": "lease",
                "epoch": session.epoch, "lease_seconds": 2}

    world.scene_post.side_effect = scene_post

    def configure(key, value):
        if value == "true":
            config_entered.set()
            assert finish_config.wait(2)
        states.append(value)
        return {"ok": True}

    body.set_config.side_effect = configure

    def enable():
        try:
            server.node_config(f"body:{session.body}", server.ConfigIn(key="armed", value="true"))
        except Exception as error:
            failures.append(error)

    def enter():
        try:
            hub.scene_tests.acquire(session.id, "tab")
        except Exception as error:
            failures.append(error)

    config_worker, entry_worker = threading.Thread(target=enable), threading.Thread(target=enter)
    original_lock = hub.body_control_lock

    def observed_lock(body_name):
        if threading.current_thread() is entry_worker:
            entry_waiting.set()
        return original_lock(body_name)

    monkeypatch.setattr(hub, "body_control_lock", observed_lock)
    config_worker.start()
    try:
        assert config_entered.wait(1)
        entry_worker.start()
        assert entry_waiting.wait(1)
        assert not world_leased.wait(0.1), "scene lease was acquired before old enable finished"
        finish_config.set()
        config_worker.join(2)
        entry_worker.join(2)
        assert not config_worker.is_alive() and not entry_worker.is_alive()
        assert not failures
        assert states == ["true", "false"]
        assert hub.scene_tests.blocks(session.body)
        assert not hub.store.get(session.id).armed
    finally:
        finish_config.set()
        for worker in (config_worker, entry_worker):
            if worker.ident is not None:
                worker.join(2)
