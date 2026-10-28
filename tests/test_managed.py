from nerv.managed import channel_home, environment_identity, lease, active_leases, storage_root


def test_channels_share_storage_not_data(tmp_path, monkeypatch):
    monkeypatch.delenv('NERV_STORAGE_HOME', raising=False)
    assert storage_root(tmp_path / 'profiles/preview') == tmp_path
    assert channel_home('preview', tmp_path) != channel_home('stable', tmp_path)


def test_identity_ignores_release_and_url_but_not_compute():
    spec = {'packages': ['a.whl']}
    plan = {'target': 'linux-x86_64', 'backend': 'cpu', 'artifacts': [
        {'name': 'a.whl', 'sha256': 'a' * 64, 'kind': 'wheel'}]}
    first = environment_identity('example', spec, {}, plan)
    plan.update(release_version='next', base_url='https://different.example')
    assert environment_identity('example', spec, {}, plan) == first
    plan['backend'] = 'cu130'
    assert environment_identity('example', spec, {}, plan) != first


def test_lease_is_released_after_exception(tmp_path):
    path = tmp_path / 'environment'
    try:
        with lease(tmp_path, [path]):
            assert active_leases(tmp_path) == [str(path)]
            raise RuntimeError('worker failed')
    except RuntimeError:
        pass
    assert active_leases(tmp_path) == []


def test_inventory_preserves_legacy_preview_and_interrupted_update(tmp_path):
    import json
    from nerv.managed import environments
    from nerv.install_system import atomic_json
    preview = tmp_path / 'profiles/preview'
    directory = preview / 'envs/host/old'
    python = directory / 'bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    record = {'python': str(python), 'version': 'old'}
    atomic_json(directory / 'nerv-install.json', record)
    snapshot = tmp_path / 'snapshots/recovery'
    atomic_json(snapshot / 'runtimes/example.json', record)
    atomic_json(preview / 'update-pending.json', {'snapshot': str(snapshot)})
    rows = environments(tmp_path)
    assert len(rows) == 1
    assert rows[0]['references'] == ['preview:rollback']
    assert rows[0]['health'] == 'needs_check'
    assert json.loads((directory / 'nerv-install.json').read_text()) == record


def test_recovery_keeps_both_host_generations(tmp_path):
    from nerv.managed import environments
    from nerv.install_system import atomic_json
    recovery = tmp_path / 'backups/interrupted'
    for name in ('active', 'previous'):
        directory = tmp_path / 'envs/host' / name
        python = directory / 'bin/python'
        python.parent.mkdir(parents=True)
        python.touch()
        record = {'python': str(python), 'version': name}
        atomic_json(directory / 'nerv-install.json', record)
        atomic_json(recovery / (name + '.json'), record)
    atomic_json(tmp_path / 'update-pending.json', {'snapshot': str(recovery)})
    assert all('stable:rollback' in row['references'] for row in environments(tmp_path))


def test_interrupted_launch_blocks_cleanup(tmp_path):
    import pytest
    with lease(tmp_path, [tmp_path / 'env'], child=True):
        pass
    with pytest.raises(ValueError, match='interrupted process launch'):
        active_leases(tmp_path)


def test_failed_spawn_releases_reservation(tmp_path):
    with lease(tmp_path, [tmp_path / 'env'], child=True) as reservation:
        reservation.failed_to_start()
    assert active_leases(tmp_path) == []


def test_child_survives_parent_crash(tmp_path):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    from nerv.managed import process_alive
    code = '''
import os, subprocess, sys
from nerv.managed import lease
with lease(sys.argv[1], [sys.argv[2]], child=True) as reservation:
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reservation.started(child.pid)
    print(child.pid, flush=True)
    os._exit(17)
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'src'), PYTHONDONTWRITEBYTECODE='1')
    parent = subprocess.run([sys.executable, '-c', code, str(tmp_path), str(tmp_path / 'env')],
                            capture_output=True, text=True, env=environment, timeout=10)
    assert parent.returncode == 17, parent.stderr
    pid = int(parent.stdout.strip())
    assert active_leases(tmp_path) == [str(tmp_path / 'env')]
    deadline = time.monotonic() + 8
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(.05)
    assert not process_alive(pid)
    assert active_leases(tmp_path) == []


def test_managed_runner_child_remains_protected_after_parent_exit(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    from nerv.managed import process_alive
    code = '''
import sys
from nerv.managed import run
run(sys.argv[1], [sys.argv[2]], [sys.executable, '-c', 'import time; time.sleep(3)'])
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'src'), PYTHONDONTWRITEBYTECODE='1')
    parent = subprocess.Popen([sys.executable, '-c', code, str(tmp_path), str(tmp_path / 'env')], env=env)
    pid = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for marker in (tmp_path / 'leases').glob('*.json'):
                pid = json.loads(marker.read_text()).get('child_pid')
                if pid:
                    break
            if pid:
                break
            time.sleep(.02)
        assert pid, 'worker did not start'
        parent.kill()
        parent.wait(timeout=5)
        assert active_leases(tmp_path) == [str(tmp_path / 'env')]
        deadline = time.monotonic() + 8
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not process_alive(pid)
        assert not active_leases(tmp_path)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
