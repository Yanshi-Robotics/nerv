"""Delayed scene commands cannot restart forces or change time after cancellation."""

from types import SimpleNamespace
import threading

import numpy as np
import pytest

from test_simulation_lifecycle import simulation as simulation_fixture
from test_scene_operator import operator as operator_fixture
from nerv.world.mujoco_node import WorldSim


simulation = simulation_fixture
operator = operator_fixture


@pytest.fixture(autouse=True)
def isolated_review_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NERV_HOME", str(tmp_path / "nerv-home"))
    monkeypatch.setenv("NERV_TRUST_ALL", "1")


def test_cancel_rejects_an_older_delayed_joint_command(simulation, operator):
    hub, session, body, world, _ = simulation
    control, _ = operator
    control.sim.epoch = session.epoch
    adapter = SimpleNamespace(scene_operator=control, _lock=threading.RLock())
    adapter.scene_state = lambda: WorldSim.scene_state(adapter)
    arrived, finish = threading.Event(), threading.Event()
    errors = []
    responses = []
    body.status.return_value = {"running_skill": "", "held": False}
    body.set_config.return_value = {"ok": True}

    def scene_post(operation, payload):
        if operation == "acquire":
            lease = control.acquire(payload["session"], payload["owner"], payload["epoch"])
            return {"ok": True, **lease, **control.state()}
        assert operation == "command"
        if payload["action"] == "joint":
            arrived.set()
            assert finish.wait(2), "test did not release the delayed older command"
        return WorldSim.scene_command(adapter, payload)

    world.scene_post.side_effect = scene_post
    acquired = hub.scene_tests.acquire(session.id, "ordered-tab")
    credentials = {key: acquired[key] for key in ("owner", "token", "epoch")}

    def old_joint():
        try:
            responses.append(
                hub.scene_tests.action(
                    session.id,
                    "command",
                    {
                        **credentials,
                        "action": "joint",
                        "joint": "ix_drawer",
                        "fraction": 1,
                        "xy": [0, 0],
                        "sequence": 1,
                    },
                )
            )
        except ValueError as error:
            errors.append(str(error))

    worker = threading.Thread(target=old_joint)
    worker.start()
    try:
        assert arrived.wait(2)
        cancelled = hub.scene_tests.action(
            session.id,
            "command",
            {
                **credentials,
                "action": "cancel",
                "sequence": 2,
            },
        )
        assert cancelled["ok"] and control.last_reason == "cancelled"
        assert not control.runtime.interaction.targets
        finish.set()
        worker.join(2)
        assert not worker.is_alive()
        control.sim.data.time = 0.3
        control.tick()
        assert not control.runtime.interaction.targets, (
            "older joint request restarted force after cancel completed"
        )
        assert np.allclose(control.sim.data.qfrc_applied, 0)
        assert errors or any(not response.get("ok") for response in responses)
    finally:
        finish.set()
        worker.join(2)
        control.revoke()


def test_cancel_rejects_an_older_time_job_at_execution(operator):
    """A queued time preset must validate order when the rendering job actually runs."""
    control, _ = operator
    arrived, finish = threading.Event(), threading.Event()
    errors = []
    responses = []
    adapter = SimpleNamespace(scene_operator=control, _lock=threading.RLock())
    adapter.scene_state = lambda: WorldSim.scene_state(adapter)
    control.runtime.set_time = lambda phase, renderer: setattr(control.runtime, "phase", phase)

    def queued_render(job):
        arrived.set()
        assert finish.wait(2), "test did not release the queued rendering job"
        return job(None)

    adapter.render = SimpleNamespace(run=queued_render)
    acquired = control.acquire("s", "ordered-tab", "current")
    credentials = {"session": "s", **{key: acquired[key] for key in ("owner", "token", "epoch")}}

    def old_time():
        try:
            responses.append(
                WorldSim.scene_command(
                    adapter,
                    {
                        **credentials,
                        "action": "time",
                        "phase": "night",
                        "sequence": 1,
                    },
                )
            )
        except ValueError as error:
            errors.append(str(error))

    worker = threading.Thread(target=old_time)
    worker.start()
    try:
        assert arrived.wait(2)
        cancelled = WorldSim.scene_command(
            adapter,
            {
                **credentials,
                "action": "cancel",
                "sequence": 2,
            },
        )
        assert cancelled["ok"] and control.last_reason == "cancelled"
        finish.set()
        worker.join(2)
        assert not worker.is_alive()
        assert control.runtime.phase == "day", (
            "queued time change executed after a newer cancellation"
        )
        assert errors or any(not response.get("ok") for response in responses)
    finally:
        finish.set()
        worker.join(2)
        control.revoke()
