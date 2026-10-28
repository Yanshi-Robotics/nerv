from pathlib import Path
from types import SimpleNamespace

from nerv import managed
from nerv.install_system import atomic_json
from nerv.platform import cli


def test_first_preview_install_uses_managed_host_and_separate_data(tmp_path, monkeypatch):
    python = tmp_path / 'envs/host/fixture/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(cli.sys, 'executable', str(python))
    monkeypatch.setattr(managed, 'storage_root', lambda *a: tmp_path)
    calls = []
    def run(root, paths, command, **kwargs):
        calls.append((root, paths, command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(managed, 'run', run)
    assert cli.cmd_preview(SimpleNamespace(arguments=['update', '--bundle', str(tmp_path / 'bundle')])) == 0
    root, resources, command, options = calls[0]
    assert command == [str(python), '-I', '-m', 'nerv.platform.cli', 'update', '--manifest', str(tmp_path / 'bundle/release.json')]
    assert options['env']['NERV_HOME'] == str(tmp_path / 'profiles/preview')
    assert options['env']['NERV_STORAGE_HOME'] == str(root)
    assert 'PYTHONPATH' not in options['env']
    assert resources == [python.parent.parent]
    assert not (tmp_path / 'active.json').exists()


def test_preview_pending_update_blocks_serve(tmp_path, monkeypatch):
    monkeypatch.setattr(managed, 'storage_root', lambda *a: tmp_path)
    atomic_json(tmp_path / 'profiles/preview/update-pending.json', {'snapshot': 'fixture'})
    monkeypatch.setattr(managed, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not launch')))
    assert cli.cmd_preview(SimpleNamespace(arguments=[])) == 1


def test_preview_does_not_launch_an_external_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(managed, 'storage_root', lambda *a: tmp_path)
    monkeypatch.setattr(cli.sys, 'executable', '/other-project/python')
    assert cli.cmd_preview(SimpleNamespace(arguments=['update', '--bundle', str(tmp_path / 'bundle')])) == 1
    assert not Path(tmp_path / 'profiles/preview').exists()
