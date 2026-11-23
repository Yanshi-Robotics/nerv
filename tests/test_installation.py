"""Release activation must preserve the last usable environment and user data."""
import hashlib
import json
import zipfile
from pathlib import Path

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


def test_preview_rollback_refuses_shared_install_transaction(tmp_path, monkeypatch):
    from filelock import FileLock, Timeout
    preview = tmp_path / 'profiles/preview'
    preview.mkdir(parents=True)
    monkeypatch.setenv('NERV_HOME', str(preview))
    monkeypatch.delenv('NERV_STORAGE_HOME', raising=False)
    with FileLock(tmp_path / 'installation-process.lock'):
        with pytest.raises(Timeout):
            i.rollback()
    assert not (preview / 'installation-process.lock').exists()


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
    monkeypatch.setattr(i.managed, 'run', lambda *args, **kwargs: None)
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
        'installed': False, 'needs_repair': False, 'download_bytes': 0,
        'installation_root': str(tmp_path / 'envs/fixture-simulator'), 'installation_path': None}]
    with pytest.raises(ValueError, match='Unknown official module'):
        module_jobs.start('arbitrary-command')
    with pytest.raises(ValueError, match='Unknown official module'):
        module_jobs.start('host')


def test_install_reports_actual_environment_before_commands(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path / 'isolated preview'))
    manifest, _ = bundle(tmp_path)
    monkeypatch.setattr(i, 'uv_executable', lambda: 'uv')
    events = []
    def run(*args):
        assert events[0]['installation_path'].startswith(str(i.home() / 'envs/host'))
    monkeypatch.setattr(i, 'run', run)
    result = i.install(manifest=manifest, progress=lambda **fields: events.append(fields))
    assert i.python_at(events[0]['installation_path']) == __import__('pathlib').Path(result['python'])
    assert events[-1] == {'phase': 'complete', 'state': 'complete'}


def test_job_phase_clears_previous_file_progress(tmp_path, monkeypatch):
    from nerv.platform import module_jobs
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    path = tmp_path / 'install-jobs/abc.json'
    i.write_json(path, {'id': 'abc', 'module': 'fixture', 'phase': 'queued', 'state': 'queued'})
    def install(module, *, cancelled, progress, **kwargs):
        progress(phase='download', asset='bundle', downloaded=100, download_total=100)
        progress(phase='dependencies')
        state = json.loads(path.read_text())
        assert state['downloaded'] is None and state['download_total'] is None
        assert state['asset'] is None
        assert state['phase_started_at'] <= state['updated_at']
        progress(state='complete', phase='complete')
    monkeypatch.setattr(i, 'install', install)
    module_jobs._work('abc')
    assert json.loads(path.read_text())['state'] == 'complete'


def test_live_log_reads_appended_output_and_bounded_tail(tmp_path, monkeypatch):
    from nerv.platform import module_jobs
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    path = tmp_path / 'install-jobs/abc.log'
    path.parent.mkdir()
    path.write_text('')
    assert module_jobs.log_tail('abc')['modified_at'] is None
    path.write_text('Downloading package\n')
    first = module_jobs.log_tail('abc')
    with path.open('a') as f:
        f.write('Installed package\n')
    second = module_jobs.log_tail('abc')
    assert second['bytes'] > first['bytes']
    assert second['log'].endswith('Installed package')
    assert second['modified_at'] >= first['modified_at']
    path.write_text('old\n' * module_jobs.LOG_TAIL_BYTES + '最后一行\n', encoding='utf-8')
    tail = module_jobs.log_tail('abc')
    assert tail['truncated'] is True
    assert tail['log'].endswith('最后一行')
    assert len(tail['log'].splitlines()) <= module_jobs.LOG_TAIL_LINES
    assert len(tail['log'].encode()) <= module_jobs.LOG_TAIL_BYTES
    with pytest.raises(ValueError):
        module_jobs.log_tail('../credentials')
    with pytest.raises(FileNotFoundError):
        module_jobs.log_tail('def')


def test_installer_network_environment_is_shared_and_allowlisted(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    env = i.installer_environment({'PATH': '/fixture', 'https_proxy': 'http://proxy.invalid',
        'NO_PROXY': 'localhost', 'UV_CONCURRENT_DOWNLOADS': '2', 'OPENAI_API_KEY': 'private',
        'PYTHONPATH': '/untrusted', 'UV_INDEX_URL': 'https://untrusted.invalid'})
    assert env['https_proxy'] == 'http://proxy.invalid'
    assert env['NO_PROXY'] == 'localhost'
    assert env['UV_CONCURRENT_DOWNLOADS'] == '2'
    assert env['UV_HTTP_TIMEOUT'] == str(i.INSTALL_READ_TIMEOUT_S)
    assert env['UV_HTTP_RETRIES'] == str(i.INSTALL_HTTP_RETRIES)
    assert env['UV_CACHE_DIR'] == str(tmp_path / '.cache/uv')
    assert not {'OPENAI_API_KEY', 'PYTHONPATH', 'UV_INDEX_URL'} & env.keys()


def test_install_job_uses_installer_network_environment(tmp_path, monkeypatch):
    from nerv.platform import module_jobs
    monkeypatch.setattr(module_jobs, 'WORKERS', {})
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.invalid')
    monkeypatch.setenv('OPENAI_API_KEY', 'private')
    manifest = {'products': {'host': {}, 'fixture': {}}}
    monkeypatch.setattr(i, 'read_manifest', lambda: (manifest, tmp_path))
    monkeypatch.setattr(module_jobs, 'catalog', lambda: {'jobs': []})
    calls = []
    def popen(*args, **kwargs):
        calls.append(kwargs)
        return type('Process', (), {'pid': 1})()
    monkeypatch.setattr(module_jobs.subprocess, 'Popen', popen)
    module_jobs.start('fixture')
    assert calls[0]['env']['HTTPS_PROXY'] == 'http://proxy.invalid'
    assert calls[0]['env']['UV_CONCURRENT_DOWNLOADS'] == str(i.INSTALL_DOWNLOAD_CONCURRENCY)
    assert 'OPENAI_API_KEY' not in calls[0]['env']


def test_install_failure_reports_dns_and_keeps_original_log(tmp_path, monkeypatch):
    from nerv.platform import module_jobs
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    path = tmp_path / 'install-jobs/abc.json'
    i.write_json(path, {'id': 'abc', 'module': 'fixture', 'phase': 'dependencies', 'state': 'running'})
    log = path.with_suffix('.log')
    output = 'Failed to download package\n dns error\n Temporary failure in name resolution\n'
    log.write_text(output)
    monkeypatch.setattr(i, 'install', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('command failed')))
    module_jobs._work('abc')
    state = json.loads(path.read_text())
    assert state['error_code'] == 'download_dns'
    assert state['state'] == 'failed'
    assert log.read_text() == output
    log.write_text('Failed to fetch: timed out')
    assert module_jobs.failure_code('abc') == 'download_failed'
    log.write_text('An incompatible package version')
    assert module_jobs.failure_code('abc') is None


def test_component_identity_reuses_environment_across_channels_and_release_labels(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / 'managed'
    monkeypatch.setenv('NERV_HOME', str(root))
    manifest, wheel = bundle(tmp_path)
    lock = manifest.parent / 'runtime.lock'
    lock.write_text('exact test dependency lock')
    value = json.loads(manifest.read_text())
    value['files'][lock.name] = {'sha256': hashlib.sha256(lock.read_bytes()).hexdigest()}
    value['products']['fixture'] = {'label': 'Fixture runtime', 'files': [wheel.name, lock.name],
                                   'packages': [wheel.name], 'lock': lock.name}
    i.write_json(manifest, value)
    commands = []
    def run(command, *args):
        commands.append(command)
        if command[1] == 'venv':
            python = i.python_at(Path(command[-1]))
            python.parent.mkdir(parents=True)
            python.touch()
    monkeypatch.setattr(i, 'run', run)
    monkeypatch.setattr(i, '_verify_record', lambda r: None)
    monkeypatch.setattr(i, 'uv_executable', lambda: 'uv')
    original = i.install('fixture', manifest=manifest)
    count = len(commands)
    value['version'] = 'another-ui-release'
    i.write_json(manifest, value)
    monkeypatch.setenv('NERV_HOME', str(root / 'profiles/preview'))
    reused = i.install('fixture', manifest=manifest)
    assert reused['python'] == original['python']
    assert len(commands) == count
    assert len(list((root / 'envs/fixture').iterdir())) == 1
    assert (root / 'runtimes/fixture.json').exists()
    assert (root / 'profiles/preview/runtimes/fixture.json').exists()


def test_archive_update_changes_resources_without_reinstalling_python(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / 'managed'
    monkeypatch.setenv('NERV_HOME', str(root))
    manifest, wheel = bundle(tmp_path)
    lock = manifest.parent / 'runtime.lock'
    lock.write_text('exact test dependency lock')
    archive = manifest.parent / 'scene.zip'
    value = json.loads(manifest.read_text())
    value['files'][lock.name] = {'sha256': hashlib.sha256(lock.read_bytes()).hexdigest()}
    value['products']['fixture'] = {'label': 'Fixture runtime', 'files': [wheel.name, lock.name, archive.name],
        'packages': [wheel.name], 'lock': lock.name, 'archive': archive.name}
    commands = []
    def run(command, *args):
        commands.append(command)
        if command[1] == 'venv':
            python = i.python_at(Path(command[-1]))
            python.parent.mkdir(parents=True)
            python.touch()
    monkeypatch.setattr(i, 'run', run)
    monkeypatch.setattr(i, '_verify_record', lambda r: None)
    monkeypatch.setattr(i, 'uv_executable', lambda: 'uv')
    records = []
    for revision in ('first', 'second'):
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('nerv-assets/test/world.yaml', json.dumps({'name': revision}))
            z.writestr('assets/scene.xml', '<mujoco/>')
        value['version'] = revision
        value['files'][archive.name] = {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        i.write_json(manifest, value)
        records.append(i.install('fixture', manifest=manifest))
    assert records[0]['python'] == records[1]['python']
    assert records[0]['registry_root'] != records[1]['registry_root']
    assert sum(c[1] == 'venv' for c in commands) == 1
    assert all(Path(r['registry_root']).is_relative_to(root / 'resources') for r in records)
    old = Path(records[0]['registry_root']) / 'nerv-assets/test/world.yaml'
    assert json.loads(old.read_text())['name'] == 'first'


def test_engine_only_module_does_not_require_bundled_worlds(tmp_path, monkeypatch):
    from nerv.platform.module_jobs import _needs_repair
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    stage = tmp_path / 'envs/sim/test'
    python = stage / 'bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    root = tmp_path / 'resources/sim'
    record = {'python': str(python), 'environment_id': 'test', 'registry_root': str(root)}
    i.write_json(stage / 'nerv-install.json', record)
    i.write_json(tmp_path / 'runtimes/sim.json', record)
    i.write_json(root / 'module.json', {'schema': 2, 'engine': 'mujoco', 'assets': 'separate', 'bodies': ['test']})
    i.write_json(root / 'bodies/test/body.yaml', {})
    assert not _needs_repair('sim', {'sim': record})
    (root / 'bodies/test/body.yaml').unlink()
    assert _needs_repair('sim', {'sim': record})


def test_project_history_snapshot_restores_prior_generation_and_preserves_newer(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    old = tmp_path / 'memory/sessions/old/events.jsonl'
    old.parent.mkdir(parents=True)
    old.write_text('original journal')
    project = tmp_path / 'projects/p_old/project.json'
    project.parent.mkdir(parents=True)
    project.write_text('original project')
    snapshot = i._snapshot()
    old.write_text('new project fields')
    project.write_text('new revision')
    (tmp_path / 'credentials.json').write_text('private unchanged')
    newer = Path(i._restore(snapshot, interrupted=False))
    assert old.read_text() == 'original journal'
    assert project.read_text() == 'original project'
    assert (newer / 'memory/sessions/old/events.jsonl').read_text() == 'new project fields'
    assert (newer / 'projects/p_old/project.json').read_text() == 'new revision'
    assert (tmp_path / 'credentials.json').read_text() == 'private unchanged'


def test_legacy_snapshot_restores_verified_pre_project_journal(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    snapshot = tmp_path / 'backups/old'
    i.write_json(snapshot / 'snapshot.json', {'paths': list(i.RESTORE_PATHS), 'schema': 1, 'rollback_depth': 1})
    i.write_json(snapshot / 'active.json', {'version': '0.8.5'})
    backup = tmp_path / 'projects/migration-backup-v1'
    backup.mkdir(parents=True)
    original = b'{"id":"old"}\n'
    (backup / 'old.json').write_bytes(original)
    i.write_json(backup / 'checksums.json', {'old.json': hashlib.sha256(original).hexdigest()})
    journal = tmp_path / 'memory/sessions/new.json'
    journal.parent.mkdir(parents=True)
    journal.write_text('{"project_id":"p_new"}')
    newer = Path(i._restore(snapshot, interrupted=False))
    assert (tmp_path / 'memory/sessions/old.json').read_bytes() == original
    assert not journal.exists() and not (tmp_path / 'projects').exists()
    assert (newer / 'memory/sessions/new.json').read_text() == '{"project_id":"p_new"}'
    assert (newer / 'projects/migration-backup-v1/old.json').read_bytes() == original
    # Repeating recovery after interruption uses the completed snapshot, not live projects.
    i._restore(snapshot, interrupted=False)
    assert (tmp_path / 'memory/sessions/old.json').read_bytes() == original


def test_corrupt_history_snapshot_fails_before_moving_live_data(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    original = tmp_path / 'memory/sessions/old.json'
    original.parent.mkdir(parents=True)
    original.write_text('original')
    snapshot = i._snapshot()
    (snapshot / 'memory/sessions/old.json').write_text('corrupt')
    with pytest.raises(ValueError, match='verification'):
        i._restore(snapshot)
    assert original.read_text() == 'original'


def test_history_hashes_skip_lock_files_and_use_posix_keys(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    sess = tmp_path / 'memory/sessions/s1.json'
    sess.parent.mkdir(parents=True)
    sess.write_text('{"id":"s1"}')
    (tmp_path / 'projects').mkdir()
    (tmp_path / 'projects/.lock').write_text('')
    (tmp_path / 'bodies/b1').mkdir(parents=True)
    (tmp_path / 'bodies/b1/display.lock').write_text('')
    hashes = i._history_hashes(tmp_path)
    assert 'memory/sessions/s1.json' in hashes
    assert all(not key.endswith('.lock') and '\\' not in key for key in hashes)
    snap = i._snapshot()
    assert not (snap / 'projects/.lock').exists()
    assert not (snap / 'bodies/b1/display.lock').exists()
