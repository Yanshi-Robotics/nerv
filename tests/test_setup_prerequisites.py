"""Fault and lifecycle checks use synthetic buses; no serial device is opened."""
from dataclasses import make_dataclass
import json
import sys
import time
from types import SimpleNamespace

import pytest
from nerv_runtime_lerobot import prerequisites as p
from nerv_runtime_lerobot.standard import MOTOR_IDS


@pytest.fixture
def device(tmp_path, monkeypatch):
    values = {name: dict(id=mid, drive_mode=0, homing_offset=0, range_min=1, range_max=4000)
              for name, mid in MOTOR_IDS.items()}
    source = tmp_path / 'student.json'
    source.write_text(json.dumps(values))
    payload = dict(port='fixture-only', id='student', calibration_dir=str(tmp_path))
    monkeypatch.setattr(p, 'context', lambda payload: {'fingerprint': __import__('hashlib').sha256(source.read_bytes()).hexdigest()})
    return payload, values, tmp_path, source


class ReadOnlyBus:
    model_number_table = {'sts3215': 777}
    def __init__(self, values):
        self.values = values
        self.closed = False
        self.port_handler = SimpleNamespace(closePort=self.close)
    def close(self):
        self.closed = True
    def connect(self, *, handshake):
        assert handshake is False
    def ping(self, mid):
        return 777
    def read(self, register, name, normalize):
        assert register == 'Operating_Mode' and normalize is False
        return 0
    def read_calibration(self):
        cls = make_dataclass('Calibration', list(next(iter(self.values.values()))))
        return {name: cls(**v) for name, v in self.values.items()}
    def __getattr__(self, name):
        if name in ('write', 'sync_write', 'write_calibration', 'configure_motors', 'setup_motor', 'set_half_turn_homings', 'disable_torque', 'enable_torque', 'disconnect'):
            pytest.fail('Forbidden device mutation: ' + name)
        raise AttributeError(name)


def test_passed_readonly_and_stale_file(device):
    payload, values, home, source = device
    original = source.read_bytes()
    bus = ReadOnlyBus(values)
    result = p.check(payload, home, factory=lambda *args: bus)
    assert result['state'] == 'passed' and bus.closed
    assert p.require_current(payload, home)['state'] == 'passed'
    assert source.read_bytes() == original
    source.write_text(original.decode() + '\n')
    with pytest.raises(ValueError, match='checking'):
        p.require_current(payload, home)


@pytest.mark.parametrize('failure,expected', [('missing', 'unmet'), ('invalid', 'unmet'), ('permission', 'unknown'), ('disconnect', 'unknown'), ('mismatch', 'unmet'), ('model', 'unmet')])
def test_faults_do_not_claim_uncalibrated(device, failure, expected):
    payload, values, home, source = device
    bus = ReadOnlyBus(values)
    if failure == 'missing':
        payload['id'] = 'no-such-id'
    elif failure == 'invalid':
        source.write_text('{}')
    elif failure == 'permission':
        def fail(**kw):
            raise PermissionError()
        bus.connect = fail
    elif failure == 'disconnect':
        bus.ping = lambda mid: None
    elif failure == 'mismatch':
        bus.values = {name: {**v, 'homing_offset': 20} for name, v in values.items()}
    elif failure == 'model':
        bus.ping = lambda mid: 999
    result = p.check(payload, home, factory=lambda *args: bus)
    assert result['state'] == expected
    if failure in ('permission', 'disconnect'):
        assert result['checks']['calibration_file']['state'] == 'passed'
        assert result['checks']['device_calibration']['state'] == 'unknown'
        assert bus.closed


def test_shared_device_ownership_across_homes(device):
    from nerv_runtime_lerobot.device_lock import device_lock
    payload, values, home, _ = device
    with device_lock(payload['port']):
        result = p.check(payload, home, factory=lambda *args: pytest.fail('must not open'))
    assert result['checks']['motors']['code'] == 'device_busy'


def test_configure_rejects_missing_prerequisites_before_writes(device):
    from nerv_runtime_lerobot.manage import manage
    payload, _, home, _ = device
    with pytest.raises(ValueError, match='prerequisites'):
        manage('configure_standard', payload, home)
    assert not (home / 'bodies').exists()


def test_environment_receipt_not_an_independent_cpu_default(tmp_path, monkeypatch):
    python = tmp_path / 'env/bin/python'
    monkeypatch.setattr(p.sys, 'executable', str(python))
    with pytest.raises(ValueError, match='managed'):
        p.inference_device()
    receipt = python.parent.parent / 'nerv-install.json'
    receipt.parent.mkdir()
    for backend, expected in [('cpu', 'cpu'), ('cu130', 'cuda')]:
        receipt.write_text(json.dumps({'platform': {'backend': backend}}))
        assert p.inference_device() == expected


def wait_finished(manager, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.get(identifier)
        if result['state'] != 'checking':
            return result
        time.sleep(.03)
    pytest.fail('check did not finish')


def test_check_process_cancel_duplicate_and_restart(tmp_path, monkeypatch):
    from nerv.platform import setup_checks as c
    monkeypatch.setattr(c.paths, 'NERV_HOME', str(tmp_path))
    monkeypatch.setattr(c, 'check_interpreter', lambda python: None)
    monkeypatch.setattr(c, 'manage', lambda *args: {'prerequisites': {'version': 1, 'module': 'timeit'}})
    # Real owned child with no hardware. Ignores input and sleeps until cancellation.
    popen = c.subprocess.Popen
    monkeypatch.setattr(c.subprocess, 'Popen', lambda args, **kw: popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kw))
    manager = c.Checks()
    job = manager.start({'provider': 'fixture', 'python': sys.executable}, {})
    with pytest.raises(ValueError, match='already'):
        manager.start({}, {})
    manager.close()
    assert manager.process is None
    result = wait_finished(manager, job['id'])
    assert result['code'] == 'cancelled'
    assert c.Checks().get(job['id'])['state'] == 'unknown'
    (tmp_path / 'setup-checks/stale.json').write_text(json.dumps({'state': 'checking'}))
    assert c.Checks().get('stale')['code'] == 'interrupted'


def test_health_uses_host_and_reports_missing_env(tmp_path, monkeypatch):
    from nerv.platform import computer_check as c
    monkeypatch.setattr(c.installation, 'home', lambda: tmp_path)
    monkeypatch.setattr(c.module_jobs, 'system_description', lambda refresh: {'facts': {'os': 'linux'}, 'plans': {'fixture': {'compatible': True, 'free_bytes': 100, 'required_free_bytes': 10}}})
    monkeypatch.setattr(c.runtime_management, 'installations', lambda: [])
    monkeypatch.setattr(c.shutil, 'which', lambda _: '/tools/curl')
    report = c.report('fixture')
    assert report['source'] == 'host' and report['can_install']
    assert not report['environment_ready'] and report['inference_device'] is None
    assert next(v for v in report['checks'] if v['key'] == 'environment')['code'] == 'environment_missing'


def test_expired_page_lease_releases_worker(tmp_path, monkeypatch):
    from nerv.platform import setup_checks as c
    monkeypatch.setattr(c.paths, 'NERV_HOME', str(tmp_path))
    monkeypatch.setattr(c, 'check_interpreter', lambda python: None)
    monkeypatch.setattr(c, 'manage', lambda *args: {'prerequisites': {'version': 1, 'module': 'timeit'}})
    popen = c.subprocess.Popen
    monkeypatch.setattr(c.subprocess, 'Popen', lambda args, **kw: popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kw))
    monkeypatch.setattr(c, 'LEASE_SECONDS', .05)
    manager = c.Checks()
    job = manager.start({'provider': 'fixture', 'python': sys.executable}, {})
    assert wait_finished(manager, job['id'])['code'] == 'page_closed'
    assert manager.process is None


def test_api_refuses_busy_device_check(monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    from nerv.platform.setup_checks import checks
    monkeypatch.setattr(server.nerv, '_bound_runtime', ('fixture',))
    client = TestClient(server.app)
    result = client.post('/api/setup-checks/fixture/start', json={})
    assert result.status_code == 409
    monkeypatch.setattr(server.nerv, '_bound_runtime', None)
    monkeypatch.setattr(checks, 'active', lambda: True)
    result = client.post('/api/setup-camera/fixture/open', json={})
    assert result.status_code == 409
    result = client.post('/api/runtime/manage', json={'python': '/fixture', 'provider': 'fixture', 'operation': 'configure', 'payload': {}})
    assert result.status_code == 409


def test_missing_device_invalidates_saved_proof(device, monkeypatch):
    payload, values, home, _ = device
    monkeypatch.setattr(p, 'context', lambda _: {'identity': None})
    result = p.check(payload, home, factory=lambda *args: ReadOnlyBus(values))
    assert result['state'] == 'unknown'
    assert p.status(payload, home)['code'] == 'device_changed'


def test_health_insufficient_disk_blocks_install(tmp_path, monkeypatch):
    from nerv.platform import computer_check as c
    monkeypatch.setattr(c.installation, 'home', lambda: tmp_path)
    monkeypatch.setattr(c.module_jobs, 'system_description', lambda refresh: {'facts': {}, 'plans': {'fixture': {'compatible': True, 'free_bytes': 1, 'required_free_bytes': 10}}})
    monkeypatch.setattr(c.runtime_management, 'installations', lambda: [])
    monkeypatch.setattr(c.shutil, 'which', lambda _: '/tools/curl')
    result = c.report('fixture')
    assert not result['can_install']
    assert next(v for v in result['checks'] if v['key'] == 'disk')['state'] == 'unmet'
