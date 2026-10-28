"""Runtime environments remain leased until their owned process has stopped."""
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from nerv import managed, paths
from nerv.platform.launcher import Launcher


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'NERV_HOME', str(tmp_path))
    monkeypatch.setenv('NERV_STORAGE_HOME', str(tmp_path))
    monkeypatch.setattr(paths, 'LOGS_DIR', str(tmp_path / 'logs'))
    monkeypatch.setattr('nerv.platform.runtime_management.check_interpreter', lambda _: None)
    instance = Launcher(Mock())
    monkeypatch.setattr(instance, '_wait_health', lambda _: {})
    return instance


def spawn(launcher, monkeypatch):
    process = Mock()
    process.pid = 12345
    process.poll.return_value = None
    process.wait.side_effect = lambda **kw: setattr(process.poll, 'return_value', 0)
    monkeypatch.setattr(managed, 'process_alive', lambda _: process.poll() is None)
    monkeypatch.setattr('nerv.platform.launcher.subprocess.Popen', lambda *a, **k: process)
    handle = launcher._spawn('tool:fixture', 'tool', sys.executable, [], 'unused')
    return handle, process


def test_live_runtime_and_stop(launcher, tmp_path, monkeypatch):
    handle, process = spawn(launcher, monkeypatch)
    assert managed.active_leases(tmp_path) == [str(Path(sys.executable).absolute().parent.parent.resolve())]
    launcher.stop(handle.name)
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
    assert managed.active_leases(tmp_path) == []


def test_unexpected_exit_releases_lease(launcher, tmp_path, monkeypatch):
    handle, process = spawn(launcher, monkeypatch)
    process.poll.return_value = 1
    assert not handle.alive()
    assert managed.active_leases(tmp_path) == []


def test_spawn_failure_releases_lease(launcher, tmp_path, monkeypatch):
    monkeypatch.setattr('nerv.platform.launcher.subprocess.Popen', Mock(side_effect=OSError('denied')))
    with pytest.raises(OSError):
        launcher._spawn('tool:fixture', 'tool', sys.executable, [], 'unused')
    assert not launcher.nodes
    assert managed.active_leases(tmp_path) == []


def test_failed_stop_retains_handle_and_lease(launcher, tmp_path, monkeypatch):
    handle, process = spawn(launcher, monkeypatch)
    process.terminate.side_effect = PermissionError('denied')
    with pytest.raises(PermissionError):
        launcher.stop(handle.name)
    assert launcher.nodes[handle.name] is handle
    assert managed.active_leases(tmp_path)
    process.terminate.side_effect = None
    launcher.stop(handle.name)


def test_health_failure_stops_and_releases(launcher, tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, '_wait_health', Mock(side_effect=TimeoutError('not ready')))
    with pytest.raises(TimeoutError):
        spawn(launcher, monkeypatch)
    assert not launcher.nodes
    assert managed.active_leases(tmp_path) == []
