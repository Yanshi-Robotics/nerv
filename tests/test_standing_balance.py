"""Controller design must not block or resurrect a revoked motor generation."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from nerv.body.balance import LocalStandingController, StandingBalance


def test_late_design_is_discarded_after_motion_or_topology_reset(tmp_path, monkeypatch):
    model = tmp_path / 'robot.xml'
    model.write_text('<mujoco/>')
    entered, finish = threading.Event(), threading.Event()
    def design(*args):
        entered.set()
        assert finish.wait(2)
        return SimpleNamespace(targets=lambda _: np.ones(1), metadata={})
    monkeypatch.setattr('nerv.body.balance.LocalStandingController', design)
    manager = StandingBalance(tmp_path, None, dict(model='robot.xml', design_timeout_s=1))
    try:
        assert manager.update(SimpleNamespace(t=1), quiet=True) is None
        assert entered.wait(1)
        worker = manager.worker
        manager.reset()
        finish.set()
        worker.join(1)
        assert manager.controller is None
        assert manager.update(SimpleNamespace(t=2), quiet=False) is None
        assert manager.status()['mode'] == 'gait'
    finally:
        finish.set()
        manager.close()


def test_model_must_stay_in_asset_root(tmp_path):
    outside = tmp_path / 'model.xml'
    outside.write_text('<mujoco/>')
    root = tmp_path / 'assets'
    root.mkdir()
    with pytest.raises(ValueError, match='active asset root'):
        StandingBalance(root, None, dict(model='../model.xml'))


def test_sensor_failures_cannot_be_used_as_zero_velocity():
    controller = LocalStandingController.__new__(LocalStandingController)
    controller.policy = SimpleNamespace(joint_names=['joint'])
    valid = dict(t=1, base_height=.8, odom_xy=[0, 0], imu_quat=[1, 0, 0, 0],
                 imu_gyro=[0, 0, 0], joint_pos=[0], joint_vel=[0])
    for key, value in [('joint_vel', []), ('imu_gyro', [0, float('nan'), 0]),
                       ('imu_quat', [0, 0, 0, 0]), ('odom_xy', [])]:
        with pytest.raises(ValueError, match='complete finite robot sensors'):
            controller._read(SimpleNamespace(**{**valid, key: value}))


def test_failed_design_is_reported_and_does_not_spin(tmp_path, monkeypatch):
    (tmp_path / 'robot.xml').write_text('<mujoco/>')
    calls = []
    def design(*args):
        calls.append(True)
        raise ValueError('invalid model')
    monkeypatch.setattr('nerv.body.balance.LocalStandingController', design)
    manager = StandingBalance(tmp_path, None, dict(model='robot.xml', design_timeout_s=1))
    try:
        manager.update(SimpleNamespace(t=1), quiet=True)
        worker = manager.worker
        if worker:
            worker.join(1)
        assert 'invalid model' in manager.status()['error']
        for _ in range(10):
            assert manager.update(SimpleNamespace(t=2), quiet=True) is None
        assert len(calls) == 1
    finally:
        manager.close()


def test_standing_handover_requires_current_generation_and_fresh_quiet():
    from nerv.body.families.humanoid import HumanoidBody
    body = HumanoidBody.__new__(HumanoidBody)
    status = dict(mode='standing', error='', generation=7)
    evidence = dict(stop_confirmed=True)
    body._balance = SimpleNamespace(status=lambda: status)
    body._stop_evidence = SimpleNamespace(snapshot=lambda: evidence)
    body._standing_generation = 6
    assert not body._standing_ready(), 'a previous controller cannot qualify this handover'
    body._standing_generation = 7
    assert body._standing_ready()
    evidence['stop_confirmed'] = False
    assert not body._standing_ready(), 'a computed target does not prove physical stopping'
    evidence['stop_confirmed'] = True
    status['error'] = 'controller unavailable'
    assert not body._standing_ready()


def test_failed_standing_controller_cannot_start_motion():
    from nerv.body.families.humanoid import HumanoidBody
    body = HumanoidBody.__new__(HumanoidBody)
    body.loco, body._held = {}, False
    body._balance = SimpleNamespace(status=lambda: dict(error='invalid dynamics'))
    result = body.invoke('move_forward', meters=1)
    assert result['ok'] is False and result['data']['executed'] is False
