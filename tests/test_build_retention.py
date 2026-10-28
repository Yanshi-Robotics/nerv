import json
import os

import pytest

from nerv import build_retention as b
from nerv.install_system import atomic_json
from nerv.install_downloads import digest
from nerv.managed import lease


def builds(root):
    result = []
    for index in range(4):
        directory = root / 'builds' / str(index)
        (directory / 'validation').mkdir(parents=True)
        (directory / 'validation/generated').write_text('reproducible')
        (directory / 'bundle').mkdir()
        (directory / 'bundle/candidate.zip').write_text('keep package')
        (directory / 'release-smoke').mkdir()
        (directory / 'release-smoke/session.json').write_text('keep data')
        (directory / 'release-smoke/envs').mkdir()
        (directory / 'release-smoke/envs/python').write_text('generated dependency')
        (directory / 'release-smoke/.cache/runtime-config').mkdir(parents=True)
        (directory / 'release-smoke/.cache/runtime-config/device.json').write_text('keep device configuration')
        (directory / 'source').mkdir()
        (directory / 'source/package-lock.json').write_text('locked fixture')
        atomic_json(directory / 'build.json', {'state': 'built', 'files': {'package-lock.json': {'sha256': digest(directory / 'source/package-lock.json')}}})
        os.utime(directory / 'build.json', (1000 + index, 1000 + index))
        result.append(directory)
    return result


def test_compaction_keeps_recent_builds_packages_and_test_data(tmp_path):
    rows = builds(tmp_path)
    report = b.compact(tmp_path)
    assert [r['path'] for r in report['candidates']] == [str(rows[1]), str(rows[0])]
    b.compact(tmp_path, report['confirmation'])
    for index, directory in enumerate(rows):
        assert (directory / 'validation').exists() == (index >= 2)
        assert (directory / 'bundle/candidate.zip').exists()
        assert (directory / 'release-smoke/session.json').exists()
        assert (directory / 'release-smoke/envs').exists() == (index >= 2)
        assert (directory / 'release-smoke/.cache/runtime-config/device.json').exists()
    assert not b.compact(tmp_path)['candidates']


def test_active_lease_and_installation_references_prevent_compaction(tmp_path):
    rows = builds(tmp_path)
    old = b.compact(tmp_path)
    atomic_json(tmp_path / 'active.json', {'python': str(rows[0] / 'validation/bin/python')})
    with lease(tmp_path, [rows[1]]):
        assert not b.compact(tmp_path)['candidates']
        with pytest.raises(ValueError, match='changed'):
            b.compact(tmp_path, old['confirmation'])


def test_interruption_keeps_recovery_plan_and_can_retry(tmp_path, monkeypatch):
    rows = builds(tmp_path)
    report = b.compact(tmp_path)
    remove = b.shutil.rmtree
    def fail(_):
        raise OSError('injected removal failure')
    monkeypatch.setattr(b.shutil, 'rmtree', fail)
    with pytest.raises(OSError):
        b.compact(tmp_path, report['confirmation'])
    assert json.loads((rows[1] / 'build.json').read_text())['compaction']['state'] == 'running'
    monkeypatch.setattr(b.shutil, 'rmtree', remove)
    retry = b.compact(tmp_path)
    b.compact(tmp_path, retry['confirmation'])
    assert not (rows[1] / 'validation').exists()


def test_current_interpreter_and_modified_recovery_source_are_retained(tmp_path, monkeypatch):
    rows = builds(tmp_path)
    monkeypatch.setattr(b.sys, 'prefix', str(rows[0] / 'validation'))
    (rows[1] / 'source/package-lock.json').write_text('changed')
    result = b.compact(tmp_path)
    assert not result['candidates']
    reasons = {row['path']: row['reasons'] for row in result['retained']}
    assert 'current-interpreter' in reasons[str(rows[0])]
    assert 'source-recovery-needs-review' in reasons[str(rows[1])]


def test_installation_mutex_and_expanded_registry_reference(tmp_path, monkeypatch):
    from filelock import FileLock, Timeout
    rows = builds(tmp_path)
    monkeypatch.setenv('NERV_TEST_BUILD_ROOT', str(rows[0]))
    registry = tmp_path / 'registry'
    config = registry / 'bodies/fixture/body.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('python: ${NERV_TEST_BUILD_ROOT}/validation/bin/python')
    atomic_json(tmp_path / 'active.json', {'python': 'external', 'registry_root': str(registry)})
    result = b.compact(tmp_path)
    assert str(rows[0]) not in [r['path'] for r in result['candidates']]
    with FileLock(str(tmp_path / 'installation-process.lock')):
        with pytest.raises(Timeout):
            b.compact(tmp_path, result['confirmation'])


def test_python_symlink_keeps_its_referenced_virtual_environment(tmp_path):
    rows = builds(tmp_path)
    base = tmp_path / 'base-python'
    base.write_text('fixture')
    executable = rows[0] / 'validation/bin/python'
    executable.parent.mkdir()
    try:
        executable.symlink_to(base)
    except OSError:
        pytest.skip('symlinks unavailable')
    atomic_json(tmp_path / 'active.json', {'python': str(executable)})
    report = b.compact(tmp_path)
    row = next(r for r in report['retained'] if r['path'] == str(rows[0]))
    assert 'installed-reference' in row['reasons']
