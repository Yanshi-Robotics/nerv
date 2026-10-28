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
                                 'frontend/node_modules/lib.js', 'worlds/build/scene.xml'])
def test_forbidden_build_input(name):
    assert not permitted(name)


def test_examples_are_build_inputs():
    assert permitted('.env.example')
    assert permitted('examples/assets/tabletop/scene.xml')
