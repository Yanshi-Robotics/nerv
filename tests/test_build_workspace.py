import json
import subprocess

import pytest

from nerv.build_workspace import permitted, snapshot


def repository(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    (path / 'main.py').write_text('original = True\n')
    subprocess.run(['git', '-C', str(path), 'add', 'main.py'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'Initial fixture'], check=True)
    return path


def test_snapshot_preserves_edits_but_excludes_untracked_and_private_files(tmp_path):
    source = repository(tmp_path / 'source')
    (source / 'main.py').write_text('changed = True\n')
    (source / 'untracked.py').write_text('not selected\n')
    for name in ('.env', '.npmrc', 'credentials.json', 'node_modules/file.js'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private fixture')
        subprocess.run(['git', '-C', str(source), 'add', name], check=True)
    destination, record = snapshot(source, tmp_path / 'managed')
    assert record['state'] == 'ready'
    assert list(record['files']) == ['main.py']
    assert (destination / 'source/main.py').read_text() == 'changed = True\n'
    assert (source / '.env').read_text() == 'private fixture'
    assert json.loads((destination / 'build.json').read_text()) == record


def test_build_storage_cannot_be_inside_source(tmp_path):
    source = repository(tmp_path / 'source')
    with pytest.raises(ValueError, match='separate'):
        snapshot(source, source / '.nerv')


def test_snapshot_rejects_external_symlink(tmp_path):
    source = repository(tmp_path / 'source')
    outside = tmp_path / 'external.py'
    outside.write_text('must not copy')
    (source / 'linked.py').symlink_to(outside)
    subprocess.run(['git', '-C', str(source), 'add', 'linked.py'], check=True)
    with pytest.raises(ValueError, match='symbolic links'):
        snapshot(source, tmp_path / 'managed')


def test_snapshot_pins_submodule_and_current_contents(tmp_path):
    child = repository(tmp_path / 'child')
    source = repository(tmp_path / 'source')
    subprocess.run(['git', '-C', str(source), '-c', 'protocol.file.allow=always',
                    'submodule', 'add', '-q', str(child), 'worlds'], check=True)
    (source / 'worlds/main.py').write_text('local asset change\n')
    destination, record = snapshot(source, tmp_path / 'managed')
    assert len(record['repositories']['worlds']) == 40
    assert (destination / 'source/worlds/main.py').read_text() == 'local asset change\n'


@pytest.mark.parametrize('name', ['../secret', '/root/secret', '.env.local',
                                 'frontend/node_modules/lib.js', 'build/lib/nerv.py'])
def test_forbidden_build_input(name):
    assert not permitted(name)


def test_examples_are_build_inputs():
    assert permitted('.env.example')
    assert permitted('examples/assets/tabletop/scene.xml')
    assert permitted('worlds/build/scene.xml')  # Indexed asset entries, not Python build output.


def test_build_environment_contains_all_caches_and_filters_external_python(tmp_path, monkeypatch):
    from nerv import build_tools
    from pathlib import Path
    monkeypatch.setenv('PYTHONPATH', '/unrelated/project')
    monkeypatch.setenv('VIRTUAL_ENV', '/course/lerobot')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-fixture')
    env = build_tools.build_environment(tmp_path)
    for key in ('UV_CACHE_DIR', 'PIP_CACHE_DIR', 'NPM_CONFIG_CACHE', 'TEMP', 'TMP', 'TMPDIR'):
        assert Path(env[key]).is_relative_to(tmp_path)
    for key in ('PYTHONPATH', 'VIRTUAL_ENV', 'OPENAI_API_KEY'):
        assert key not in env


@pytest.mark.parametrize('failure', [False, True])
def test_build_installs_locked_frontend_dependencies_before_packaging(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from nerv import build_workspace, build_tools
    directory = tmp_path / 'build'
    directory.mkdir()
    (directory / 'bundle').mkdir()
    monkeypatch.setattr(build_workspace, 'snapshot', lambda *_: (directory, {'state': 'ready'}))
    tools = {'python': str(tmp_path / 'python/bin/python'), 'node': str(tmp_path / 'node/bin/node'), 'npm': 'npm-cli.js'}
    monkeypatch.setattr(build_tools, 'prepare', lambda *_: (tools, {}))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 2:
            (directory / 'bundle/release.json').write_text('{"version":"fixture"}')
        return SimpleNamespace(returncode=1 if failure else 0)
    monkeypatch.setattr(build_workspace.subprocess, 'run', run)
    if failure:
        with pytest.raises(ValueError, match='Candidate build failed'):
            build_workspace.build(tmp_path / 'source', tmp_path)
        assert len(calls) == 1
        assert json.loads((directory / 'build.json').read_text())['state'] == 'failed'
    else:
        result = build_workspace.build(tmp_path / 'source', tmp_path)
        assert result['state'] == 'built'
        assert result['version'] == 'fixture'
        assert len(calls) == 2
    assert calls[0] == [tools['node'], tools['npm'], 'ci', '--ignore-scripts']
