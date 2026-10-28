import hashlib
import json
from pathlib import Path

import pytest

from nerv import policy_sources as sources


def test_hub_resolution_pins_commit_and_skips_remote_code(monkeypatch):
    content = b'{"type":"act"}'
    commit = 'a' * 40
    blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    calls = []
    def read(path, token):
        calls.append(path)
        if path.startswith('/api/'):
            return json.dumps({'sha': commit, 'private': True, 'siblings': [
                {'rfilename': 'config.json', 'size': len(content), 'blobId': blob},
                {'rfilename': 'model.safetensors', 'lfs': {'sha256': 'b' * 64, 'size': 123}},
                {'rfilename': 'modeling.py'}, {'rfilename': '.gitattributes'}]}).encode()
        return content
    monkeypatch.setattr(sources, 'read_metadata', read)
    plan = sources.resolve('hf://owner/model', 'candidate')
    assert plan['commit'] == commit and plan['private']
    assert [f['path'] for f in plan['files']] == ['config.json', 'model.safetensors']
    assert all('/resolve/' + commit + '/' in f['url'] for f in plan['files'])
    assert calls[1] == '/owner/model/resolve/' + commit + '/config.json'


def test_pinned_git_blob_mismatch_fails(monkeypatch):
    monkeypatch.setattr(sources, 'read_metadata', lambda path, token: json.dumps({
        'sha': 'a' * 40, 'siblings': [{'rfilename': 'config.json', 'size': 2, 'blobId': 'b' * 40}]
    }).encode() if path.startswith('/api/') else b'{}')
    with pytest.raises(ValueError, match='pinned inventory'):
        sources.resolve('owner/model')


@pytest.mark.parametrize('value', ['../model', 'https://evil.org/owner/model', 'owner/model/tree/main', 'hf://owner/../model'])
def test_repository_rejects_ambiguous_sources(value):
    with pytest.raises(ValueError):
        sources.repository(value)


def test_download_is_channel_local_and_verifies_reuse(tmp_path, monkeypatch):
    payload = b'weights'
    sha = hashlib.sha256(payload).hexdigest()
    plan = {'repository': 'owner/model', 'commit': 'a' * 40, 'private': True,
            'files': [{'path': 'checkpoint/model.safetensors', 'name': sha, 'sha256': sha, 'bytes': len(payload)}]}
    homes = []
    def transfer(home, items, **kwargs):
        homes.append(home)
        cached = home / 'cached'
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(payload)
        kwargs['progress'](downloaded=len(payload), download_total=len(payload), phase='download')
        return {items[0]['sha256']: cached}
    monkeypatch.setattr(sources, 'download_all', transfer)
    progress = []
    home = tmp_path / 'profiles' / 'preview'
    result = sources.download(home, plan, report=lambda **v: progress.append(v))
    assert Path(result['path']).is_relative_to(home)
    assert homes == [home / 'models']
    assert progress[-1]['downloaded'] == progress[-1]['download_total'] == len(payload)
    sources.download(home, plan)
    (Path(result['path']) / 'checkpoint/model.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='snapshot changed'):
        sources.download(home, plan)


def test_token_is_private_and_removed_on_failure(tmp_path):
    target = None
    with pytest.raises(RuntimeError):
        with sources.authorization(tmp_path, 'test-token') as path:
            target = path
            assert path.stat().st_mode & 0o077 == 0
            assert path.read_text() == 'Authorization: Bearer test-token\n'
            raise RuntimeError()
    assert not target.exists()
    with pytest.raises(ValueError):
        with sources.authorization(tmp_path, 'bad\nheader'):
            pass


def test_hub_job_resume_keeps_original_commit(tmp_path, monkeypatch):
    from nerv import installation
    from nerv.platform import module_jobs
    from nerv.vlm.store import CredentialStore
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    record = tmp_path / 'install-jobs/abc.json'
    original = {'repository': 'owner/model', 'commit': 'a' * 40, 'files': []}
    installation.write_json(record, {'id': 'abc', 'kind': 'policy', 'module': 'huggingface:owner/model',
        'repository': 'owner/model', 'revision': 'main', 'phase': 'download', 'state': 'paused',
        'plan_snapshot': original})
    monkeypatch.setattr(CredentialStore, 'resolve', lambda self, key: None)
    monkeypatch.setattr(sources, 'resolve', lambda *a: pytest.fail('Moving branch was resolved again'))
    def download(home, plan, **kwargs):
        assert plan == original
        return {'path': str(tmp_path / 'files'), 'commit': plan['commit']}
    monkeypatch.setattr(sources, 'download', download)
    module_jobs._work('abc')
    final = json.loads(record.read_text())
    assert final['state'] == 'complete'
    assert final['result']['commit'] == original['commit']


def test_hub_job_interruption_keeps_pinned_plan(tmp_path, monkeypatch):
    from nerv import installation
    from nerv.platform import module_jobs
    from nerv.install_downloads import DownloadStopped
    from nerv.vlm.store import CredentialStore
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    record = tmp_path / 'install-jobs/abc.json'
    plan = {'repository': 'owner/model', 'commit': 'b' * 40, 'files': []}
    installation.write_json(record, {'id': 'abc', 'kind': 'policy', 'module': 'huggingface:owner/model',
        'repository': 'owner/model', 'revision': 'main', 'phase': 'queued', 'state': 'queued'})
    monkeypatch.setattr(CredentialStore, 'resolve', lambda self, key: None)
    monkeypatch.setattr(sources, 'resolve', lambda *a: plan)
    monkeypatch.setattr(sources, 'download', lambda *a, **k: (_ for _ in ()).throw(DownloadStopped()))
    module_jobs._work('abc')
    final = json.loads(record.read_text())
    assert final['state'] == 'paused' and final['plan_snapshot'] == plan
    assert 'result' not in final
