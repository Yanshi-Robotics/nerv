"""Runtime qualification cannot leak to another policy, scene or task generation."""
from types import SimpleNamespace
import hashlib
import threading

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
        tools=lambda: [{'name':n,'kind':'skill'} for n in ['walk','turn']], invoke=invoke)
    return value, calls


def test_changed_policy_rejects_transition_even_after_cancellation(tmp_path):
    value, calls = runtime(tmp_path)
    assert value.invoke('walk')['ok']
    value.cancel_current()  # New task/claim cannot reset transition qualification.
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
