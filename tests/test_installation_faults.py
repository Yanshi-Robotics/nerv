"""Installation fault boundaries: no external network, devices or model calls."""
import json
import subprocess
import sys

import pytest

from nerv import install_downloads as downloads, install_system as systems


@pytest.mark.parametrize('target', [[], {}, None, 123])
def test_invalid_target_is_validation_error_and_preserves_settings(tmp_path, target):
    path = tmp_path / 'installation-settings.json'
    systems.atomic_json(path, systems.DEFAULTS)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        systems.save_settings(tmp_path, {'target': target})
    assert path.read_bytes() == before


def test_guardian_removes_marker_when_executable_cannot_start(tmp_path):
    marker = tmp_path / 'owned.alive'
    marker.touch()
    result = subprocess.run([sys.executable, '-c', downloads.CURL_GUARDIAN,
                             str(marker), str(tmp_path / 'missing-curl')],
                            input=b'', capture_output=True, timeout=5)
    assert result.returncode != 0
    assert not marker.exists()


def test_verified_file_clears_stale_retry_reason(monkeypatch, tmp_path):
    artifact = {'name': 'fixture.whl', 'bytes': 1, 'sha256': 'a' * 64}
    def transfer(root, item, report, **kwargs):
        report(received=0, state='retrying', reason='Connection interrupted', next_retry_at=1)
        report(received=1, state='verified', next_retry_at=None)
        return tmp_path / 'checked'
    monkeypatch.setattr(downloads, 'transfer', transfer)
    reports = []
    downloads.download_all(tmp_path, [artifact], env=None, preferences=systems.DEFAULTS,
                           progress=lambda **value: reports.append(value))
    assert reports[-1]['files'][0].get('reason') is None
    assert reports[-1]['downloaded'] == 1


def test_newer_queued_job_is_not_misclassified_as_interrupted(tmp_path, monkeypatch):
    from nerv import installation
    from nerv.platform import module_jobs as jobs, runtime_management
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    monkeypatch.setattr(jobs, 'WORKERS', {})
    monkeypatch.setattr(runtime_management, 'installations', lambda: [])
    monkeypatch.setattr(installation, 'read_manifest', lambda: ({'products': {}}, tmp_path))
    systems.atomic_json(tmp_path / 'install-jobs/a.json',
                        {'id':'a', 'module':'test', 'state':'queued', 'created_at':0})
    # A live process can still be between spawn and acquisition of the running lock.
    class Worker:
        def poll(self): return None
    jobs.WORKERS['a'] = Worker()
    assert jobs.catalog()['jobs'][0]['state'] == 'queued'
    assert json.loads((tmp_path / 'install-jobs/a.json').read_text())['state'] == 'queued'
