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
