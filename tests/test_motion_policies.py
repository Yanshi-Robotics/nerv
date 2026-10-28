"""Policy records are versioned; incomplete deployment contracts stay blocked."""
import json

import pytest

from nerv import paths
from nerv.install_downloads import digest
from nerv.install_system import atomic_json
from nerv.motion_policies import import_policy, inspect, resolve_binding, validate_contract
from nerv.platform.registry import Registry


def source(path):
    path.mkdir()
    (path / 'policy.onnx').write_bytes(b'fixture, not a loadable model')
    contract = {'joint_names': ['one'], 'default_joint_pos': [0],
                'term_scales': {'joint_pos_rel': 1}, 'action': {'scale': .5}, 'timing': {'policy_dt_s': .02}}
    release = {'policy_id': 'fixture', 'family': 'humanoid', 'robot': 'fixture/robot',
               'files': {'policy.onnx': {'sha256': digest(path / 'policy.onnx')}},
               'lin_cmd_deadband': .2, 'turn_vx': 0, 'command_ranges': {k: [-1, 1] for k in ('vx', 'vy', 'wz')}}
    (path / 'contract.json').write_text(json.dumps(contract))
    (path / 'release.yaml').write_text(json.dumps(release))
    return path, contract, release


def test_import_is_read_only_and_old_versions_do_not_follow_current(tmp_path):
    path, _, _ = source(tmp_path / 'source')
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    home = tmp_path / 'home'
    first = import_policy(home, 'fixture', path)
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before
    second = import_policy(home, 'fixture', path, 'Different starting conditions')
    assert first['revision'] != second['revision']
    assert inspect(home, 'fixture', first['revision']) == first
    assert inspect(home, 'fixture') == second
    with pytest.raises(ValueError, match='no-motion'):
        resolve_binding(home, 'fixture', 'humanoid', 'fixture/robot')
    (path / 'policy.onnx').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        inspect(home, 'fixture')


def test_ambiguous_contract_and_missing_semantics_are_not_defaulted(tmp_path):
    path, contract, release = source(tmp_path / 'source')
    (path / 'contract.yaml').write_text('{}')
    with pytest.raises(ValueError, match='ambiguity'):
        import_policy(tmp_path / 'home', 'fixture', path)
    validate_contract(contract, release)
    release['turn_vx'] = None
    with pytest.raises(ValueError, match='turn_vx'):
        validate_contract(contract, release)
    release['family'] = 'quadruped'
    with pytest.raises(ValueError, match='no installed motion controller'):
        validate_contract(contract, release)


def test_registry_resolves_managed_policy_after_module_overlay(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(paths, 'NERV_HOME', str(home))
    path, _, _ = source(tmp_path / 'source')
    record = import_policy(home, 'fixture', path)
    roots = [tmp_path / 'base', tmp_path / 'module']
    for root in roots:
        directory = root / 'bodies/robot'
        directory.mkdir(parents=True)
        data = {'name': 'robot', 'family': 'humanoid', 'policy_robot': 'fixture/robot',
                'skills': {'walk': {'policy': 'managed:fixture'}} if root == roots[0] else {},
                'buses': {'sim': {'kind': 'zmq'}}}
        (directory / 'body.yaml').write_text(json.dumps(data))
    assert Registry([str(p) for p in roots]).body('robot').policy_error
    receipt = home / 'motion-policies/fixture' / ('check-' + record['revision'] + '.json')
    atomic_json(receipt, {'state': 'loaded', 'revision': record['revision']})
    body = Registry([str(p) for p in roots]).body('robot')
    assert not body.policy_error
    assert body.skills['walk'].policy == str(path)
    assert body.skills['walk'].policy_record == record
    with pytest.raises(ValueError, match='different robot'):
        resolve_binding(home, 'fixture', 'humanoid', 'another/robot')


def test_explicit_selection_pins_revision_and_checks_robot(tmp_path):
    from nerv.motion_policies import manage, selected_binding
    path, _, _ = source(tmp_path / 'source')
    home = tmp_path / 'home'
    first = import_policy(home, 'custom', path)
    with pytest.raises(ValueError, match='no-motion'):
        manage(home, 'select', {'id': 'custom'})
    atomic_json(home / 'motion-policies/custom' / ('check-' + first['revision'] + '.json'),
                {'state': 'loaded', 'revision': first['revision']})
    manage(home, 'select', {'id': 'custom'})
    import_policy(home, 'custom', path, 'new unreviewed conditions')
    assert selected_binding(home, 'missing-default', 'humanoid', 'fixture/robot') == first
    assert not manage(home, 'policies', {})['policies'][0]['selected']
