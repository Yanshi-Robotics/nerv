"""Runtime qualification cannot leak to another policy, scene or task generation."""
from types import SimpleNamespace
import hashlib
import threading
import pytest

from nerv.runtime.builtin import BuiltinRuntime
from nerv.body.skills import StopFlag


def runtime(tmp_path):
    hashes = {}
    for name in ('policy.onnx', 'contract.json', 'release.yaml'):
        (tmp_path/name).write_text(name)
        hashes[name] = hashlib.sha256(name.encode()).hexdigest()
    contract = {'validated': True, 'scenes': ['fixture-scene'], 'pairs': [['walk','turn']], 'policy_sha256':hashes}
    binding = SimpleNamespace(mode='sim', configuration={'skill_transitions':contract, 'scene_profile':'fixture-scene'})
    profile = SimpleNamespace(id='robot_A', family='humanoid', configuration={})
    value = BuiltinRuntime(profile, binding)
    calls = []
    def invoke(name, **kwargs):
        calls.append(name)
        return {'ok':True, 'data':{'stop_confirmed':True}}
    value.stop = StopFlag()
    value._impl = SimpleNamespace(policy=SimpleNamespace(dir=str(tmp_path)), runner=SimpleNamespace(_control=threading.local()),
        hold=lambda reason: {'ok':True,'stop_confirmed':True},
        status=lambda: {'stop_evidence':{'stop_confirmed':True}}, release=lambda: {'ok':True},
        tools=lambda: [{'name':n,'kind':'skill'} for n in ['walk','turn']], invoke=invoke)
    return value, calls


def test_changed_policy_rejects_transition_even_after_cancellation(tmp_path):
    value, calls = runtime(tmp_path)
    assert value.invoke('walk')['ok']
    value.cancel_current()  # New task/claim cannot reset transition qualification.
    assert value.release()['ok']  # A confirmed hold release must not erase the previous skill.
    (tmp_path/'policy.onnx').write_text('replacement')
    assert not value.invoke('turn')['ok']
    assert calls == ['walk']


def test_scene_and_confirmed_stop_are_required(tmp_path):
    value, calls = runtime(tmp_path)
    assert value.invoke('walk')['ok']
    value.binding.configuration['scene_profile'] = 'another-scene'
    assert not value.invoke('turn')['ok']
    value.binding.configuration['scene_profile'] = 'fixture-scene'
    assert value.invoke('turn')['ok']
    value._last_skill_stopped = False
    assert not value.invoke('turn')['ok']
    assert calls == ['walk','turn']


def test_observation_timing_qualification_is_scene_scoped(tmp_path):
    value, _ = runtime(tmp_path)
    value._supervision_config = {'version':'fixture-1', 'scenes':['fixture-scene'],
                                'max_observation_age_s':40, 'timeout_s':20, 'poll_s':.05}
    assert value.external_supervision_config()['validated']
    value.binding.configuration['scene_profile'] = 'unqualified-scene'
    assert not value.external_supervision_config()['validated']
    assert not value.supervise(1)['ok']


@pytest.mark.parametrize('next_skill', ['walk', 'turn'])
def test_old_stop_receipt_cannot_authorize_motion_after_balance_is_lost(tmp_path, next_skill):
    value, calls = runtime(tmp_path)
    assert value.invoke('walk')['ok']
    value._impl.status = lambda:{'stop_evidence':{'stop_confirmed':False}}
    result = value.invoke(next_skill)
    assert not result['ok'] and result['data']['stop_state'] == 'stop_unconfirmed'
    assert calls == ['walk']
    value._impl.status = lambda:{}
    assert not value.invoke(next_skill)['ok'], 'Missing evidence cannot authorize movement'
    value._impl.status = lambda:{'stop_evidence':{'stop_confirmed':True}}
    assert value.invoke(next_skill)['ok']


def test_standing_qualification_binds_profile_code_and_actual_world_timing(tmp_path):
    import json
    import importlib.metadata
    from pathlib import Path
    import nerv.runtime.builtin as builtin
    value, _ = runtime(tmp_path)
    config = {'actuators': {'standing_balance': {'model': 'fixture.xml'}},
              'locomotion': {'walk_speed': .6}, 'sensors': []}
    value.profile.configuration = config
    value._impl._balance = SimpleNamespace(physics_dt=.002)
    value._impl.policy.policy_dt = .02
    contract = value.binding.configuration['skill_transitions']
    root = Path(builtin.__file__).resolve().parents[1]
    contract.update(profile_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        physics_dt_s=.002, policy_dt_s=.02,
        implementation_sha256={name: hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in ['body/balance.py', 'body/families/humanoid.py', 'body/stopping.py',
                         'body/bus_zmq.py', 'runtime/builtin.py', 'world/mujoco_node.py',
                         'world/composition.py', 'world/bus_server.py', 'world/clearance.py',
                         'body/sensor_state.py', 'world/camera_metadata.py',
                         'world/robot_camera_mounts.py', 'world/own_camera_geometry.py']},
        dependency_versions={name:importlib.metadata.version(name) for name in ['mujoco','numpy','onnxruntime']})
    assert value.skill_transition_config()['validated']
    config['sensors'].append({'name':'camera:ground','camera':'ground_down'})
    assert not value.skill_transition_config()['validated'], 'A changed sensor configuration requires new qualification'
    config['sensors'].clear()
    assert value.skill_transition_config()['validated']
    for name in ('world/robot_camera_mounts.py','world/own_camera_geometry.py'):
        original=contract['implementation_sha256'].pop(name)
        assert not value.skill_transition_config()['validated'], 'Missing camera compilation or capture code is unqualified'
        contract['implementation_sha256'][name]='0'*64
        assert not value.skill_transition_config()['validated'], 'Changed camera code cannot inherit old qualification'
        contract['implementation_sha256'][name]=original
        assert value.skill_transition_config()['validated']
    config['locomotion']['walk_speed'] = .7
    assert not value.skill_transition_config()['validated']
    config['locomotion']['walk_speed'] = .6
    value._impl._balance.physics_dt = .005
    assert not value.skill_transition_config()['validated']
    value._impl._balance.physics_dt = .002
    contract['implementation_sha256']['body/balance.py'] = '0' * 64
    assert not value.skill_transition_config()['validated']
