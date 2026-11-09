"""No simulation needed: a policy finishing after hold must not write a target."""
import threading
from types import SimpleNamespace

import numpy as np

from nerv.body.families.humanoid import HumanoidBody
from nerv.body.skills import StopFlag


def test_inference_started_before_hold_cannot_overwrite_latched_targets():
    entered, finish = threading.Event(), threading.Event()
    writes = []
    body = HumanoidBody.__new__(HumanoidBody)
    state = SimpleNamespace(t=1.0, odom_yaw=0.0)
    body.bus = SimpleNamespace(read=lambda: state, write=lambda command: writes.append(command.targets))
    body.runner = SimpleNamespace(stop=StopFlag())
    body.loco = {"sim_dead_wall_s": 1.0}
    body._hold_lock = threading.Lock()
    body._held, body._hold_pending, body._hold_reason = False, None, ""
    body._hold_targets, body._last_targets = [], [0.0]
    body._yaw0, body._state, body._running = None, state, True
    body._fallen = lambda _: False
    body._observation = lambda *_: (np.zeros(1), None)
    body._set_command = lambda *_: None

    def infer(_):
        entered.set()
        assert finish.wait(2)
        body._running = False
        return np.ones(1)

    body.policy = SimpleNamespace(policy_dt=.01, joint_names=["fixture_joint"], act=infer,
                                  obs_dim=1, default_pos=np.zeros(1), action_scale=1.0)
    worker = threading.Thread(target=body._policy_loop)
    worker.start()
    assert entered.wait(1)
    body._hold("operator", state)
    finish.set()
    worker.join(2)
    assert not worker.is_alive()
    assert writes == [[0.0]], "late policy inference overwrote the emergency hold"


def test_hold_requires_sustained_quiet_not_one_gait_turning_point():
    body = HumanoidBody.__new__(HumanoidBody)
    body.loco = {"hold_still_rad_s": .3, "hold_still_s": .2}
    body._hold_still_since = None
    assert not body._quiet_hold_ready(SimpleNamespace(t=1., joint_vel=[.29]))
    assert not body._quiet_hold_ready(SimpleNamespace(t=1.1, joint_vel=[.4]))
    assert not body._quiet_hold_ready(SimpleNamespace(t=1.2, joint_vel=[.02]))
    assert not body._quiet_hold_ready(SimpleNamespace(t=1.3, joint_vel=[.02]))
    assert body._quiet_hold_ready(SimpleNamespace(t=1.41, joint_vel=[.02]))
    assert not body._quiet_hold_ready(SimpleNamespace(t=1.42, joint_vel=[float('nan')]))
    assert not body._quiet_hold_ready(SimpleNamespace(t=1.43, joint_vel=[]))


def test_normal_settling_waits_for_quiet_without_extending_emergency_hold():
    body = HumanoidBody.__new__(HumanoidBody)
    body.loco = dict(hold_still_rad_s=.3, hold_still_s=.2, hold_settle_s=1.5,
                     motion_settle_s=4., sim_dead_wall_s=1., poll_s=0.)
    body._fallen = lambda _: False
    stamp = 0.
    def read():
        nonlocal stamp
        stamp += .05
        return SimpleNamespace(t=stamp, joint_vel=[.4 if stamp<1.7 else .02])
    body.bus = SimpleNamespace(read=read)
    ok, state = body._settle_motion()
    assert ok and 1.9 <= state.t < 2.1
    assert body.loco['hold_settle_s'] == 1.5
    stamp = 0.
    body.bus.read = lambda: SimpleNamespace(t=read().t, joint_vel=[.02,float('nan')])
    ok, state = body._settle_motion()
    assert not ok and state.t >= 4.
