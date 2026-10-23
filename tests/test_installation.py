"""Release activation must preserve the last usable environment and user data."""
import hashlib
import json
import zipfile

import pytest
from nerv import installation as i


def bundle(tmp_path):
    root = tmp_path / 'release'
    root.mkdir()
    wheel = root / 'example.whl'
    wheel.write_bytes(b'local test wheel')
    manifest = {'schema': 1, 'version': 'test', 'products': {
        'host': {'label': 'NERV', 'files': [wheel.name], 'packages': [wheel.name]}},
        'files': {wheel.name: {'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}}}
    i.write_json(root / 'release.json', manifest)
    return root / 'release.json', wheel


def test_checksum_failure_preserves_active_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path / 'home'))
    i.write_json(i.home() / 'active.json', {'version': 'old'})
    secret = i.home() / 'credentials.json'
    secret.write_text('private test fixture')
    manifest, wheel = bundle(tmp_path)
    wheel.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        i.install(manifest=manifest)
    assert json.loads((i.home() / 'active.json').read_text()) == {'version': 'old'}
    assert secret.read_text() == 'private test fixture'
    assert not (i.home() / 'installation.lock').exists()


def test_cancel_before_environment_creation(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path / 'home'))
    manifest, _ = bundle(tmp_path)
    with pytest.raises(InterruptedError):
        i.install(manifest=manifest, cancelled=lambda: True)
    assert not (i.home() / 'active.json').exists()


def test_failed_dependency_install_keeps_previous(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path / 'home'))
    manifest, _ = bundle(tmp_path)
    i.write_json(i.home() / 'active.json', {'version': 'old'})
    monkeypatch.setattr(i, 'uv_executable', lambda: 'uv')
    def fail(*args):
        raise RuntimeError('dependency failure')
    monkeypatch.setattr(i, 'run', fail)
    with pytest.raises(RuntimeError, match='dependency'):
        i.install(manifest=manifest)
    assert json.loads((i.home() / 'active.json').read_text())['version'] == 'old'


def test_reject_archive_escape_and_symlinks(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escape', 'bad')
    with pytest.raises(ValueError, match='Unsafe'):
        i.unpack(archive, tmp_path / 'module')
    with zipfile.ZipFile(archive, 'w') as z:
        info = zipfile.ZipInfo('link')
        info.external_attr = 0o120777 << 16
        z.writestr(info, '/etc/passwd')
    with pytest.raises(ValueError, match='Unsafe'):
        i.unpack(archive, tmp_path / 'module')


def test_rollback_refuses_active_server(tmp_path, monkeypatch):
    from filelock import FileLock, Timeout
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    with FileLock(tmp_path / 'host-running.lock'):
        with pytest.raises(Timeout):
            i.rollback()


def test_rollback_restores_release_and_keeps_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    environment = tmp_path / 'envs/old'
    python = i.python_at(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    old = {'python': str(python), 'version': 'old'}
    i.write_json(tmp_path / 'previous.json', old)
    i.write_json(environment / 'release.json', {'schema': 1, 'version': 'old', 'products': {'host': {}}})
    (tmp_path / 'credentials.json').write_text('kept')
    monkeypatch.setattr(i.subprocess, 'run', lambda *args, **kwargs: None)
    assert i.rollback() == old
    assert json.loads((tmp_path / 'release.json').read_text())['version'] == 'old'
    assert (tmp_path / 'credentials.json').read_text() == 'kept'


def test_local_candidate_caches_optional_assets(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path / 'home'))
    manifest, _ = bundle(tmp_path)
    optional = manifest.parent / 'optional.zip'
    optional.write_bytes(b'fixture optional asset')
    value = json.loads(manifest.read_text())
    value['files'][optional.name] = {'sha256': hashlib.sha256(optional.read_bytes()).hexdigest()}
    i.write_json(manifest, value)
    monkeypatch.setattr(i, 'uv_executable', lambda: 'uv')
    monkeypatch.setattr(i, 'run', lambda *args: None)
    i.install(manifest=manifest)
    assert (i.home() / '.cache/releases/test/optional.zip').read_bytes() == optional.read_bytes()
    assert not (i.home() / 'runtimes').exists()  # Cache is not an installed component.


def test_component_catalog_comes_from_release_manifest(tmp_path, monkeypatch):
    from nerv.platform import module_jobs, runtime_management
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    monkeypatch.setattr(runtime_management, 'installations', lambda: [])
    i.write_json(tmp_path / 'release.json', {'schema': 1, 'version': 'test', 'products': {
        'host': {'label': 'Host'}, 'fixture-simulator': {'label': 'Fixture simulation', 'files': []}}})
    assert module_jobs.catalog()['modules'] == [{'id': 'fixture-simulator', 'label': 'Fixture simulation',
        'installed': False, 'download_bytes': 0}]
    with pytest.raises(ValueError, match='Unknown official module'):
        module_jobs.start('arbitrary-command')
    with pytest.raises(ValueError, match='Unknown official module'):
        module_jobs.start('host')
