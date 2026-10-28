"""Preview profiles must not launch the main or a foreign environment."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('run_preview', Path(__file__).parents[1] / 'scripts/run_preview.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_profile_command_is_isolated_and_explicit(tmp_path):
    python = tmp_path / 'envs/host/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / 'active.json').write_text(json.dumps({'python': str(python)}))
    root, command = helper.preview_command(tmp_path, 8100)
    assert root == tmp_path
    assert command == [str(python), '-I', '-m', 'nerv.platform.cli', 'serve', '--port', '8100', '--no-browser']
    outside = tmp_path.parent / 'foreign-python'
    outside.touch()
    (tmp_path / 'active.json').write_text(json.dumps({'python': str(outside)}))
    with pytest.raises(ValueError, match='Install the candidate'):
        helper.preview_command(tmp_path, 8100)


def test_main_home_rejected_before_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    with pytest.raises(ValueError, match='main NERV home'):
        helper.preview_command(tmp_path / '.nerv', 8100)
