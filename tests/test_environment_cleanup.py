import json
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from nerv.environment_cleanup import prune
from nerv.install_system import atomic_json
from nerv.managed import lease


def environment(root, name, identity=True):
    directory = root / 'envs' / 'fixture' / name
    python = directory / 'bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    record = {'python': str(python), 'version': name}
    if identity:
        record['environment_id'] = name
    atomic_json(directory / 'nerv-install.json', record)
    return directory, record


def test_cleanup_requires_exact_plan_and_preserves_data(tmp_path):
    obsolete, _ = environment(tmp_path, 'old')
    current, receipt = environment(tmp_path, 'current')
    atomic_json(tmp_path / 'active.json', receipt)
    atomic_json(tmp_path / 'credentials.json', {'fixture': 'private-value'})
    snapshot = (tmp_path / 'credentials.json').read_bytes()
    report = prune(tmp_path)
    assert [v['path'] for v in report['candidates']] == [str(obsolete)]
    assert obsolete.exists() and current.exists()
    result = prune(tmp_path, report['confirmation'])
    assert result['removed'] == [str(obsolete)]
    assert not obsolete.exists() and current.exists()
    assert (tmp_path / 'credentials.json').read_bytes() == snapshot


def test_new_lease_invalidates_cleanup_confirmation(tmp_path):
    directory, _ = environment(tmp_path, 'unused')
    report = prune(tmp_path)
    with lease(tmp_path, [directory]):
        with pytest.raises(ValueError, match='changed'):
            prune(tmp_path, report['confirmation'])
    assert directory.exists()


def test_config_and_cross_channel_rollback_are_protected(tmp_path):
    device, record = environment(tmp_path, 'device')
    previous, old = environment(tmp_path, 'previous')
    config = tmp_path / 'bodies/robot/body.yaml'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'buses': {'real': {'python': record['python']}}}))
    atomic_json(tmp_path / 'profiles/preview/previous.json', old)
    report = prune(tmp_path)
    assert report['candidates'] == []
    assert {r['path'] for r in report['retained']} == {str(device), str(previous)}


def test_legacy_ownership_and_shared_python_are_retained(tmp_path):
    base, _ = environment(tmp_path, 'base')
    child, _ = environment(tmp_path, 'child', identity=False)
    (child / 'pyvenv.cfg').write_text('home = ' + str(base / 'bin'))
    report = prune(tmp_path)
    assert not report['candidates']
    assert any('python-dependency' in row['reasons'] for row in report['retained'])
    assert any('legacy-ownership-needs-review' in row['reasons'] for row in report['retained'])


def test_running_old_host_or_installation_blocks_deletion(tmp_path):
    directory, _ = environment(tmp_path, 'old')
    confirmation = prune(tmp_path)['confirmation']
    with FileLock(str(tmp_path / 'host-running.lock')):
        with pytest.raises(Timeout):
            prune(tmp_path, confirmation)
    with FileLock(str(tmp_path / 'installation-process.lock')):
        with pytest.raises(Timeout):
            prune(tmp_path, confirmation)
    assert directory.exists()


def test_linked_external_environment_is_never_a_candidate(tmp_path):
    outside = tmp_path / 'other-project'
    directory, _ = environment(outside, 'external')
    root = tmp_path / 'nerv'
    parent = root / 'envs/fixture'
    parent.mkdir(parents=True)
    (parent / 'linked').symlink_to(directory, target_is_directory=True)
    report = prune(root)
    assert not report['candidates']
    assert 'outside-managed-environments' in report['retained'][0]['reasons']
    assert 'linked-environment-needs-review' in report['retained'][0]['reasons']
    prune(root, report['confirmation'])
    assert (directory / 'bin/python').exists()


def test_receipt_changes_invalidate_review(tmp_path):
    directory, _ = environment(tmp_path, 'unused')
    report = prune(tmp_path)
    path = directory / 'nerv-install.json'
    data = json.loads(path.read_text())
    data['changed'] = True
    atomic_json(path, data)
    with pytest.raises(ValueError, match='changed'):
        prune(tmp_path, report['confirmation'])
    assert Path(directory).is_dir()


def test_nested_snapshot_configs_commands_and_extra_registry(tmp_path, monkeypatch):
    _, record = environment(tmp_path, 'nested')
    _, extra = environment(tmp_path, 'extra')
    recovery = tmp_path / 'backups/one'
    nested = tmp_path / 'backups/two'
    atomic_json(tmp_path / 'previous.json', {'snapshot': str(recovery)})
    atomic_json(recovery / 'previous.json', {'snapshot': str(nested)})
    atomic_json(nested / 'bodies/robot/body.yaml', {'buses': {'sim': {'command': [record['python'], '-m', 'worker']}}})
    # A cycle must terminate without dropping references.
    atomic_json(nested / 'previous.json', {'snapshot': str(recovery)})
    custom = tmp_path / 'external'
    atomic_json(custom / 'tools/fixture/tool.yaml', {'python': extra['python']})
    monkeypatch.setenv('NERV_REGISTRY_PATHS', str(custom))
    report = prune(tmp_path)
    assert not report['candidates']
    assert {reason for row in report['retained'] for reason in row['reasons']} == {'stable:rollback', 'external-registry'}


def test_missing_snapshot_blocks_cleanup(tmp_path):
    environment(tmp_path, 'unused')
    atomic_json(tmp_path / 'previous.json', {'snapshot': str(tmp_path / 'missing')})
    with pytest.raises(ValueError, match='snapshot is missing'):
        prune(tmp_path)


def test_build_python_and_internal_alias_are_retained(tmp_path):
    base, _ = environment(tmp_path, 'base')
    linked, _ = environment(tmp_path, 'aliased')
    (linked.parent / 'alias').symlink_to(linked, target_is_directory=True)
    cfg = tmp_path / 'toolchains/build/fixture/python/pyvenv.cfg'
    cfg.parent.mkdir(parents=True)
    cfg.write_text('home = ' + str(base / 'bin'))
    report = prune(tmp_path)
    assert not report['candidates']
    assert len(report['retained']) == 2


def test_failed_removal_has_persistent_journal(tmp_path, monkeypatch):
    import nerv.environment_cleanup as cleanup
    environment(tmp_path, 'unused')
    report = prune(tmp_path)
    monkeypatch.setattr(cleanup.shutil, 'rmtree', lambda _: (_ for _ in ()).throw(PermissionError('file in use')))
    result = prune(tmp_path, report['confirmation'])
    saved = json.loads(Path(result['journal']).read_text())
    assert saved['state'] == saved['items'][0]['state'] == 'failed'
    assert 'file in use' in saved['items'][0]['reason']


@pytest.mark.parametrize('pointer,record', [
    ('active.json', {}), ('previous.json', {'version': 'broken'}),
    ('runtimes/robot.json', {'provider': 'fixture'}), ('update-pending.json', {'phase': 'staging'}),
])
def test_incomplete_reference_blocks_cleanup(tmp_path, pointer, record):
    environment(tmp_path, 'unused')
    atomic_json(tmp_path / pointer, record)
    with pytest.raises(ValueError, match='Incomplete installation reference'):
        prune(tmp_path)


def test_partial_removal_remains_visible_after_receipt_is_lost(tmp_path, monkeypatch):
    import nerv.environment_cleanup as cleanup
    directory, _ = environment(tmp_path, 'unused')
    report = prune(tmp_path)
    def partial(path):
        (path / 'nerv-install.json').unlink()
        raise PermissionError('file in use')
    monkeypatch.setattr(cleanup.shutil, 'rmtree', partial)
    result = prune(tmp_path, report['confirmation'])
    new = prune(tmp_path)
    assert not new['candidates']
    assert new['maintenance'][0]['path'] == str(directory)
    assert new['maintenance'][0]['journal'] == result['journal']


def test_interrupted_removal_is_protected(tmp_path):
    directory, _ = environment(tmp_path, 'unused')
    atomic_json(tmp_path / 'maintenance/cleanup/interrupted.json', {
        'state': 'running', 'items': [{'path': str(directory), 'state': 'removing'}]})
    report = prune(tmp_path)
    assert not report['candidates']
    assert report['maintenance'][0]['state'] == 'removing'
    assert 'incomplete-cleanup-needs-review' in report['retained'][0]['reasons']
