"""Dependency installation validates capabilities separately from scene assets."""
import json

import pytest

from nerv.runtime import module_check


def test_runtime_only_module_has_no_implicit_scene_or_policy(tmp_path, monkeypatch):
    descriptor = {'schema': 2, 'engine': 'mujoco', 'bodies': ['test-arm'], 'assets': 'separate'}
    (tmp_path / 'module.json').write_text(json.dumps(descriptor))
    body = tmp_path / 'bodies/test-arm/body.yaml'
    body.parent.mkdir(parents=True)
    body.write_text(json.dumps({'name': 'test-arm', 'family': 'arm', 'buses': {'sim': {'kind': 'zmq'}}}))
    monkeypatch.setattr(module_check, 'check_engine', lambda: {'verified_worlds': [], 'task_success': None})
    result = module_check.check(tmp_path)
    assert result['asset_installation'] == 'separate'
    assert result['verified_worlds'] == []
    assert result['task_success'] is None
    body.write_text(json.dumps({'name': 'test-arm', 'family': 'arm', 'buses': {'real': {'kind': 'lerobot'}}}))
    with pytest.raises(ValueError, match='invalid robot binding'):
        module_check.check(tmp_path)
