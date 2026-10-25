"""Real curl transfers against a local fault server; no external files or hardware."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from nerv import install_downloads as d, install_system as s, install_plan as p

PAYLOAD = b'install-resume-contract\n' * 10000


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        calls = []
        mode = 'drop'

        def log_message(self, *args):
            pass

        def do_GET(self):
            cls = type(self)
            offset = int(self.headers.get('Range', 'bytes=0-').split('=')[1].split('-')[0])
            cls.calls.append(offset)
            if cls.mode == 'rate' and len(cls.calls) == 1:
                self.send_response(429)
                self.send_header('Retry-After', '0')
                self.end_headers()
                return
            if cls.mode == 'missing':
                self.send_response(404)
                self.end_headers()
                return
            if cls.mode == 'ignore':
                offset = 0
            if cls.mode == 'range416' and offset:
                cls.mode = 'normal'
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206 if offset else 200)
            self.send_header('Content-Length', str(len(PAYLOAD) - offset))
            self.send_header('ETag', '"fixture"')
            if offset:
                self.send_header('Content-Range', f'bytes {offset}-{len(PAYLOAD)-1}/{len(PAYLOAD)}')
            self.end_headers()
            output = PAYLOAD[offset:]
            if cls.mode == 'corrupt':
                output = b'x' * len(output)
            if cls.mode == 'drop' and len(cls.calls) == 1:
                self.wfile.write(output[:len(output)//2])
                self.wfile.flush()
                self.close_connection = True
            else:
                self.wfile.write(output)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield http, Handler
    http.shutdown()
    http.server_close()
    thread.join()


def item(server):
    return {'name': 'fixture.whl', 'url': f'http://127.0.0.1:{server[0].server_port}/fixture',
            'bytes': len(PAYLOAD), 'sha256': hashlib.sha256(PAYLOAD).hexdigest()}


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(d, 'retry_delay', lambda *args: 0)


def test_interrupted_body_really_resumes(server, tmp_path):
    artifact = item(server)
    path = d.transfer(tmp_path, artifact, allow_http=True)
    assert path.read_bytes() == PAYLOAD
    assert server[1].calls == [0, len(PAYLOAD)//2]
    d.transfer(tmp_path, artifact, allow_http=True)
    assert len(server[1].calls) == 2


@pytest.mark.parametrize('mode', ['ignore', 'range416', 'normal'])
def test_saved_prefix_restart_and_server_range_behavior(mode, server, tmp_path):
    artifact = item(server)
    _, partial, meta = d.cache_paths(tmp_path, artifact)
    partial.parent.mkdir(parents=True)
    partial.write_bytes(PAYLOAD[:12345])
    meta.write_text(json.dumps({'etag': '"fixture"'}))
    server[1].mode = mode
    result = d.transfer(tmp_path, artifact, allow_http=True)
    assert result.read_bytes() == PAYLOAD
    assert server[1].calls[0] == 12345


def test_pause_preserves_prefix_and_resume(server, tmp_path):
    artifact = item(server)
    paused = []
    def report(**fields):
        if fields.get('state') == 'retrying':
            paused.append(True)
    with pytest.raises(d.DownloadStopped):
        d.transfer(tmp_path, artifact, allow_http=True, report=report, stopped=lambda: 'paused' if paused else None)
    assert d.cache_paths(tmp_path, artifact)[1].stat().st_size == len(PAYLOAD)//2
    assert d.transfer(tmp_path, artifact, allow_http=True).read_bytes() == PAYLOAD


@pytest.mark.parametrize('mode', ['corrupt', 'missing'])
def test_invalid_artifacts_never_verified(mode, server, tmp_path):
    server[1].mode = mode
    artifact = item(server)
    with pytest.raises(ValueError):
        d.transfer(tmp_path, artifact, allow_http=True)
    assert not d.checked_file(tmp_path, artifact)
    assert len(server[1].calls) <= 2


def test_rate_limit_and_aggregate_progress(server, tmp_path):
    server[1].mode = 'rate'
    artifact = item(server)
    progress = []
    d.download_all(tmp_path, [artifact, artifact], env=None, preferences=s.DEFAULTS,
                   progress=lambda **v: progress.append(v), allow_http=True)
    assert progress[-1]['download_total'] == len(PAYLOAD)
    assert progress[-1]['downloaded'] == len(PAYLOAD)
    assert progress[-1]['concurrency'] == 1
    assert len(server[1].calls) == 2


def test_official_origins_required(tmp_path, server):
    with pytest.raises(ValueError, match='origin'):
        d.transfer(tmp_path, item(server))


def facts():
    return {'os': 'linux', 'arch': 'x86_64', 'process_bits': 64, 'libc': 'glibc', 'libc_version': '2.35',
            'gpu': {'devices': []}}


def test_manual_platform_cannot_override_abi(tmp_path):
    with pytest.raises(ValueError, match='conflicts'):
        s.save_settings(tmp_path, {'target': 'windows-x86_64'}, facts())
    assert s.save_settings(tmp_path, {'target': 'linux-x86_64'}, facts())['target'] == 'linux-x86_64'
    with pytest.raises(ValueError):
        s.save_settings(tmp_path, {'concurrency': True}, facts())
    s.save_settings(tmp_path, {'proxy': 'http://user:private@proxy.invalid'}, facts())
    assert 'private' not in json.dumps(s.settings(tmp_path, public=True))


def test_cpu_auto_and_incompatible_nvidia():
    cpu = {'target': 'linux-x86_64', 'backend': 'cpu', 'available': True}
    gpu = {'target': 'linux-x86_64', 'backend': 'cu130', 'available': True, 'validated': True,
           'minimum_driver_major': 580, 'minimum_compute_capability': 7.5}
    manifest = {'download_plans': {'lerobot0.6.1': {'linux-x86_64/cpu': cpu, 'linux-x86_64/cu130': gpu}}}
    assert p.select(manifest, 'lerobot0.6.1', s.DEFAULTS, facts()) == cpu
    with pytest.raises(ValueError, match='NVIDIA'):
        p.select(manifest, 'lerobot0.6.1', {**s.DEFAULTS, 'compute': 'cu130'}, facts())


def test_crashed_worker_recovers_uncommitted_response(server, tmp_path):
    artifact = item(server)
    target, partial, _ = d.cache_paths(tmp_path, artifact)
    target.parent.mkdir(parents=True)
    partial.write_bytes(PAYLOAD[:100])
    target.with_suffix('.attempt').write_text(json.dumps({'offset': 100, 'validator': '"fixture"'}))
    target.with_suffix('.headers').write_text(f'HTTP/1.1 206 Partial Content\nContent-Range: bytes 100-{len(PAYLOAD)-1}/{len(PAYLOAD)}\nETag: "fixture"\n\n')
    target.with_suffix('.chunk').write_bytes(PAYLOAD[100:1000])
    server[1].mode = 'normal'
    assert d.transfer(tmp_path, artifact, allow_http=True).read_bytes() == PAYLOAD
    assert server[1].calls == [1000]


def test_offline_missing_file_makes_no_request(server, tmp_path):
    with pytest.raises(d.DownloadWaiting, match='Offline'):
        d.transfer(tmp_path, item(server), allow_http=True, env={'UV_OFFLINE': '1'})
    assert server[1].calls == []


def test_invalid_range_does_not_append(server, tmp_path):
    artifact = item(server)
    target, partial, meta = d.cache_paths(tmp_path, artifact)
    target.parent.mkdir(parents=True)
    partial.write_bytes(PAYLOAD[:100])
    target.with_suffix('.attempt').write_text(json.dumps({'offset': 100}))
    target.with_suffix('.headers').write_text(f'HTTP/1.1 206 Partial Content\nContent-Range: bytes 99-{len(PAYLOAD)-1}/{len(PAYLOAD)}\n\n')
    target.with_suffix('.chunk').write_bytes(b'invalid fragment')
    d.recover_response(target, partial, meta, artifact)
    assert partial.read_bytes() == PAYLOAD[:100]


def test_concurrent_same_file_downloads_once(server, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    server[1].mode = 'normal'
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(d.transfer, tmp_path, item(server), allow_http=True) for _ in range(2)]
        assert all(job.result().read_bytes() == PAYLOAD for job in jobs)
    assert server[1].calls == [0]


def test_space_check_includes_extraction_even_with_cache(server, tmp_path, monkeypatch):
    artifact = item(server)
    d.transfer(tmp_path, artifact, allow_http=True)
    from types import SimpleNamespace
    monkeypatch.setattr(d.shutil, 'disk_usage', lambda _: SimpleNamespace(free=len(PAYLOAD)))
    with pytest.raises(ValueError, match='disk space'):
        d.download_all(tmp_path, [artifact], env=None, preferences=s.DEFAULTS)


def test_macos_minimum_version_is_enforced():
    profile = {'target':'macos-aarch64','backend':'cpu','available':True,'macos_min':'14.0'}
    manifest = {'download_plans': {'host': {'macos-aarch64/cpu': profile}}}
    mac = {**facts(), 'os':'macos','arch':'aarch64','macos_version':'13.7'}
    with pytest.raises(ValueError, match='macOS'):
        p.select(manifest,'host',s.DEFAULTS,mac)
    assert p.select(manifest,'host',s.DEFAULTS,{**mac,'macos_version':'14.0'}) == profile


def test_resuming_job_keeps_plan_and_rejects_duplicate(tmp_path, monkeypatch):
    from nerv.platform import module_jobs as jobs
    monkeypatch.setenv('NERV_HOME', str(tmp_path))
    path=tmp_path/'install-jobs/abc.json'
    snapshot={'target':'linux-x86_64','backend':'cpu'}
    s.atomic_json(path, {'id':'abc','state':'paused','plan_snapshot':snapshot})
    path.with_suffix('.pause').touch()
    monkeypatch.setattr(jobs,'catalog',lambda:{'jobs':[json.loads(path.read_text())]})
    calls=[]
    monkeypatch.setattr(jobs,'_spawn',lambda id:calls.append(id) or {'id':id})
    jobs.resume('abc')
    assert json.loads(path.read_text())['plan_snapshot']==snapshot
    assert not path.with_suffix('.pause').exists()
    with pytest.raises(ValueError,match='active'):
        jobs.resume('abc')
    assert calls==['abc']


def test_transfer_guardian_stops_child_when_owner_pipe_closes(tmp_path):
    import subprocess
    import sys
    marker=tmp_path/'owned.alive'
    marker.touch()
    guardian=subprocess.Popen([sys.executable,'-c',d.CURL_GUARDIAN,str(marker),sys.executable,'-c','import time; time.sleep(60)'],stdin=subprocess.PIPE)
    try:
        # Closing the pipe also happens in the kernel when an installation worker crashes.
        guardian.stdin.close()
        guardian.wait(timeout=5)
        assert not marker.exists()
    finally:
        if guardian.poll() is None:
            guardian.kill()
            guardian.wait()
