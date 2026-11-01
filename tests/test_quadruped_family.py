"""Quadruped contracts, measured feedback and dispatch bounds without hardware."""
from copy import deepcopy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from nerv.body import families
from nerv.body.families.humanoid import DEFAULT_LOCOMOTION, PolicyRelease
from nerv.body.families.quadruped import QuadrupedBody
from nerv.body.skills import SkillRunner, StopFlag
from nerv.motion_policies import validate_contract
from nerv.nerve.world import BusState

ROOT = Path(__file__).resolve().parents[1]


def historical_release():
    root = ROOT / 'policies/go2-velocity-flat'
    if not root.is_dir():
        pytest.skip('Optional historical Go2 source is absent')
    return root, json.loads((root / 'contract.json').read_text()), yaml.safe_load((root / 'release.yaml').read_text())


def test_quadruped_registered_with_disabled_hardware():
    assert families.load('quadruped').QuadrupedBody is QuadrupedBody
    data = yaml.safe_load((ROOT / 'bodies/quadruped-unitree-go2/body.yaml').read_text())
    assert data['family'] == 'quadruped'
    assert data['policy_robot'] == 'unitree/go2'
    assert data['buses']['real']['enabled'] is False
    assert data['actuators']['deployment_status'] == 'needs_validation'
    assert len(data['actuators']['foot_bodies']) == 4


def test_historical_go2_missing_semantics_blocks_before_bus_access():
    root, contract, release = historical_release()
    with pytest.raises(ValueError, match='lin_cmd_deadband|command ranges'):
        validate_contract(contract, release)
    spec = {'policy_robot': 'unitree/go2', 'skills': {'move_forward': {'policy': str(root)}}}
    bus = SimpleNamespace(spawn=lambda _: pytest.fail('Incomplete contract opened the bus'))
    with pytest.raises(ValueError, match='lin_cmd_deadband|command ranges'):
        QuadrupedBody(spec, bus, SkillRunner(StopFlag()))


def test_quadruped_accepts_complete_metadata_not_guessed_in_controller():
    _, contract, release = historical_release()
    release = deepcopy(release)
    # A test fixture only; these values are never added to the historical release.
    release.update(lin_cmd_deadband=0.0, command_ranges={k: [-1, 1] for k in ('vx', 'vy', 'wz')})
    validate_contract(contract, release)
    release['command_ranges']['vx'][1] = math.inf
    with pytest.raises(ValueError, match='finite'):
        validate_contract(contract, release)


def test_legacy_term_order_is_respected_before_inference(tmp_path, monkeypatch):
    root, contract, release = historical_release()
    contract['term_order'] = list(reversed(contract['term_order']))
    (tmp_path / 'contract.json').write_text(json.dumps(contract))
    (tmp_path / 'release.yaml').write_text(yaml.safe_dump(release))
    (tmp_path / 'policy.onnx').write_bytes(b'fixture')
    import onnxruntime
    monkeypatch.setattr(onnxruntime, 'InferenceSession', lambda *a, **kw: SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(name='obs')]))
    policy = PolicyRelease(str(tmp_path))
    assert policy.term_order == tuple(contract['term_order'])
    body = QuadrupedBody.__new__(QuadrupedBody)
    body.policy = policy
    import threading
    body._cmd_lock = threading.Lock()
    body._cmd = np.zeros(3)
    st = BusState(1, policy.default_pos.tolist(), [0.] * 12, imu_quat=[1., 0., 0., 0.])
    obs, _ = body._observation(st, None, np.ones(12))
    assert obs.shape == (45,)
    assert np.all(obs[:12] == 1), 'Last action must be first when declared first'


@pytest.mark.parametrize('argument', [math.nan, math.inf, -math.inf, 0, -1])
@pytest.mark.parametrize('verb,field', [('move_forward', 'meters'), ('turn_left', 'degrees')])
def test_invalid_distance_and_angle_never_dispatch(argument, verb, field):
    body = QuadrupedBody.__new__(QuadrupedBody)
    body.loco = dict(DEFAULT_LOCOMOTION)
    body._held = False
    body.runner = SimpleNamespace(run=lambda *a: pytest.fail('Invalid action dispatched'))
    assert body.invoke(verb, **{field: argument})['ok'] is False


def test_observation_has_sensor_feedback_not_world_truth():
    body = QuadrupedBody.__new__(QuadrupedBody)
    body.bus = SimpleNamespace(read=lambda: BusState(1, [], [], odom_xy=[20, 30], odom_yaw=.2))
    body._yaw0 = 0
    body._clearances = lambda: {'front': 1.0}
    body._front_cone = lambda: 1.0
    body._fallen = lambda _: False
    body._held = False
    body._hold_reason = body._policy_error = ''
    body._last = 'stopped'
    body.sensors = []
    state, images = body.observe()
    assert state['heading_deg'] == 11.5 and state['last_action'] == 'stopped'
    assert not {'odom_xy', 'x', 'y', 'room', 'room_label'}.intersection(state)
    assert images == []


def test_complete_policy_still_requires_quadruped_stop_profile(tmp_path):
    _, contract, release = historical_release()
    release.update(lin_cmd_deadband=0.0, command_ranges={k: [-1, 1] for k in ('vx', 'vy', 'wz')})
    (tmp_path / 'contract.json').write_text(json.dumps(contract))
    (tmp_path / 'release.yaml').write_text(yaml.safe_dump(release))
    spec = {'policy_robot': 'unitree/go2', 'skills': {'move_forward': {'policy': str(tmp_path)}}}
    with pytest.raises(ValueError, match='motion/stop settings'):
        QuadrupedBody(spec, SimpleNamespace(spawn=lambda _: pytest.fail('Missing profile spawned')), SkillRunner(StopFlag()))


@pytest.mark.parametrize('output', [np.array([math.nan]), np.array([math.inf]), np.zeros(2)])
def test_invalid_network_output_never_reaches_bus(output):
    import threading
    body = QuadrupedBody.__new__(QuadrupedBody)
    writes = []
    state = SimpleNamespace(t=1., odom_yaw=0.)
    body.bus = SimpleNamespace(read=lambda: state, write=lambda cmd: writes.append(cmd))
    body._hold_lock = threading.Lock()
    body._held, body._hold_pending = False, None
    body._running, body._yaw0 = True, None
    body._fallen = lambda _: False
    body._observation = lambda *_: (np.zeros(1), None)
    body.loco = {'sim_dead_wall_s': 1.}
    def act(_):
        body._running = False
        return output
    body.policy = SimpleNamespace(policy_dt=.001, joint_names=['one'], act=act,
                                  obs_dim=1, default_pos=np.zeros(1), action_scale=1.)
    body._policy_loop()
    assert writes == []
    assert 'invalid action' in body._policy_error
