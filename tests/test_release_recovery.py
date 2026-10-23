"""Fault injection for coordinated host/runtime activation; no package downloads."""
import json
from pathlib import Path

import pytest

from nerv import installation as i


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('NERV_HOME', str(tmp_path / '用户 home'))
    root = i.home()
    for product in ('host', 'device'):
        python = i.python_at(root / 'envs' / product / 'old')
        python.parent.mkdir(parents=True)
        python.touch()
        record = {'python': str(python), 'version': 'old', 'product': product}
        i.write_json(root / ('active.json' if product == 'host' else 'runtimes/device.json'), record)
    i.write_json(root / 'bodies/fixture/body.yaml', {'buses': {'real': {'python': str(i.python_at(root / 'envs/device/old'))}}})
    i.write_json(root / 'schemes/original/1.json', {'name': 'original'})
    i.write_json(root / 'vlm.json', {'models': {'fixture': {'name': 'original'}}})
    i.write_json(root / 'credentials.json', {'key': 'original fixture'})
    i.write_json(root / 'release.json', {'schema': 1, 'version': 'old', 'products': {'host': {}, 'device': {}}})
    manifest = tmp_path / 'candidate.json'
    i.write_json(manifest, {'schema': 1, 'version': 'new', 'config_schema': 1, 'products': {'host': {}, 'device': {}}})
    def install(product='host', **kwargs):
        assert kwargs['activate'] is False
        # No active pointer changes until EVERY component passes its checks.
        assert json.loads((root / 'active.json').read_text())['version'] == 'old'
        python = i.python_at(root / 'envs' / product / 'new')
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        return {'python': str(python), 'version': 'new', 'product': product}
    monkeypatch.setattr(i, 'install', install)
    monkeypatch.setattr(i.subprocess, 'run', lambda *args, **kwargs: None)
    return root, manifest


def test_update_and_rollback_restore_all_components_and_archive_new_config(tmp_path, monkeypatch):
    root, manifest = setup(tmp_path, monkeypatch)
    result = i.update(manifest=manifest)
    assert result['updated_components'] == ['device']
    binding = json.loads((root / 'bodies/fixture/body.yaml').read_text())['buses']['real']['python']
    assert binding == str(i.python_at(root / 'envs/device/new'))
    i.write_json(root / 'schemes/later/1.json', {'name': 'new work'})
    i.write_json(root / 'credentials.json', {'key': 'changed fixture'})
    i.write_json(root / 'vlm.json', {'models': {'fixture': {'name': 'changed'}}})
    i.write_json(root / 'memory/session.json', {'event': 'later'})
    result = i.rollback()
    assert json.loads((root / 'runtimes/device.json').read_text())['version'] == 'old'
    assert json.loads((root / 'active.json').read_text())['version'] == 'old'
    assert not (root / 'schemes/later').exists()
    assert (Path(result['preserved_newer_configuration']) / 'schemes/later/1.json').is_file()
    assert json.loads((root / 'credentials.json').read_text())['key'] == 'changed fixture'
    assert json.loads((root / 'vlm.json').read_text())['models']['fixture']['name'] == 'original'
    assert (root / 'memory/session.json').exists()


def test_activation_failure_restores_all_pointers(tmp_path, monkeypatch):
    root, manifest = setup(tmp_path, monkeypatch)
    original = i.write_json
    def fail(path, value):
        if Path(path) == root / 'active.json' and value.get('version') == 'new':
            raise OSError('injected disk failure')
        original(path, value)
    monkeypatch.setattr(i, 'write_json', fail)
    with pytest.raises(OSError, match='injected'):
        i.update(manifest=manifest)
    assert json.loads((root / 'active.json').read_text())['version'] == 'old'
    assert json.loads((root / 'runtimes/device.json').read_text())['version'] == 'old'
    assert not (root / 'update-pending.json').exists()


def test_interrupted_update_recovery_and_unknown_schema(tmp_path, monkeypatch):
    root, manifest = setup(tmp_path, monkeypatch)
    snapshot = i._snapshot()
    i.write_json(root / 'update-pending.json', {'snapshot': str(snapshot), 'phase': 'activating'})
    i.write_json(root / 'active.json', {'version': 'broken'})
    with pytest.raises(ValueError, match='interrupted'):
        i.update(manifest=manifest)
    assert i.rollback()['recovered']
    assert json.loads((root / 'active.json').read_text())['version'] == 'old'
    i.write_json(root / 'config-schema.json', {'schema': 900})
    with pytest.raises(ValueError, match='migration'):
        i.update(manifest=manifest)
    assert not (root / 'update-pending.json').exists()


def test_unknown_installed_component_blocks_update(tmp_path, monkeypatch):
    root, manifest = setup(tmp_path, monkeypatch)
    i.write_json(root / 'runtimes/custom.json', {'python': 'external'})
    with pytest.raises(ValueError, match='installed components'):
        i.update(manifest=manifest)
