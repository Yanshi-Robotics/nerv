"""Checksum-addressed, resumable curl transfers, independent of uv's private cache."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

CONNECT_TIMEOUT = 60
IDLE_TIMEOUT = 180
RETRIES = 5
WAIT_BUDGET = 900
POLL_SECONDS = .2
PROGRESS_SECONDS = 1
CHUNK_SIZE = 1024 * 1024
OFFICIAL_HOSTS = {'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com',
                  'files.pythonhosted.org', 'pypi.org', 'download.pytorch.org', 'download-r2.pytorch.org'}


class DownloadWaiting(RuntimeError):
    pass


class DownloadStopped(InterruptedError):
    def __init__(self, state='paused'):
        self.state = state
        super().__init__('Download ' + state)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(CHUNK_SIZE):
            value.update(data)
    return value.hexdigest()


def cache_paths(root, item):
    key = item['sha256']
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Invalid artifact checksum')
    parent = Path(root) / '.cache/downloads/sha256' / key[:2]
    return parent / key, parent / (key + '.partial'), parent / (key + '.json')


def checked_file(root, item):
    path, _, _ = cache_paths(root, item)
    return path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256']


def headers(path):
    if not path.exists():
        return 0, {}
    # The final response follows proxy CONNECT and redirect headers.
    blocks = path.read_text(errors='replace').replace('\r\n', '\n').split('\n\n')
    final = next((v for v in reversed(blocks) if v.startswith('HTTP/')), '')
    lines = final.splitlines()
    if not lines:
        return 0, {}
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        return 0, {}
    values = {k.strip().lower(): v.strip() for line in lines[1:] if ':' in line for k, v in [line.split(':', 1)]}
    return status, values


def retry_delay(value, attempt):
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = min(60, 2 ** attempt) + random.uniform(0, 1)
    return max(0, delay)


def curl_executable():
    result = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
    if not result:
        raise ValueError('curl is required for recoverable downloads. Install curl, then retry.')
    version = subprocess.run([result, '--version'], capture_output=True, text=True, timeout=5)
    if version.returncode or 'https' not in version.stdout.lower():
        raise ValueError('curl must support HTTPS')
    return result


def recover_response(target, partial, metadata, item):
    """Recover a response left by a terminated worker, before opening new output files."""
    chunk, head, attempt = target.with_suffix('.chunk'), target.with_suffix('.headers'), target.with_suffix('.attempt')
    if not chunk.exists() or not attempt.exists():
        return
    saved = json.loads(attempt.read_text())
    offset = saved['offset']
    status, fields = headers(head)
    size = chunk.stat().st_size
    valid = status == 200 and size <= item['bytes']
    if status == 206:
        import re
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', fields.get('content-range', ''))
        new_validator = fields.get('etag') or fields.get('last-modified')
        valid = bool(match and int(match[1]) == offset and int(match[3]) == item['bytes']
                     and int(match[2]) < item['bytes'] and size <= int(match[2]) - offset + 1
                     and (not saved.get('validator') or not new_validator or saved['validator'] == new_validator))
        if not partial.exists() or partial.stat().st_size != offset:
            valid = False  # A previous atomic commit may already contain this response.
    if valid and size:
        joined = target.with_suffix('.joined')
        with joined.open('wb') as output:
            if status == 206:
                with partial.open('rb') as prefix:
                    shutil.copyfileobj(prefix, output, CHUNK_SIZE)
            with chunk.open('rb') as source:
                shutil.copyfileobj(source, output, CHUNK_SIZE)
        os.replace(joined, partial)
        metadata.write_text(json.dumps({k: fields[k] for k in ('etag', 'last-modified') if k in fields}))
    chunk.unlink(missing_ok=True)
    head.unlink(missing_ok=True)
    attempt.unlink(missing_ok=True)


# A pipe guardian owns curl. If the install worker crashes, stdin closes and
# the guardian stops its child before a replacement worker can resume the file.
CURL_GUARDIAN = r"""
import subprocess, sys, threading, time, signal
from pathlib import Path
marker = Path(sys.argv[1])
stop = threading.Event()
def watch():
    sys.stdin.buffer.read()
    stop.set()
threading.Thread(target=watch, daemon=True).start()
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: stop.set())
try:
    child = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL)
    while child.poll() is None and not stop.wait(.1):
        pass
    if child.poll() is None:
        child.terminate()
        try: child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    code = child.returncode if child.returncode >= 0 else 128 - child.returncode
except OSError:
    code = 126
finally:
    marker.unlink(missing_ok=True)
sys.exit(code)
"""


def transfer(root, item, *, env=None, stopped=lambda: None, report=lambda **_: None,
             bandwidth=0, allow_http=False):
    from filelock import FileLock, Timeout
    target, partial, metadata = cache_paths(root, item)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = item['url']
    parsed = urlsplit(url)
    if not (allow_http and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        if parsed.scheme != 'https' or parsed.hostname not in OFFICIAL_HOSTS or parsed.username or parsed.password:
            raise ValueError('Artifact origin is not an approved official HTTPS source')
    if Path(item['name']).name != item['name'] or type(item['bytes']) is not int or item['bytes'] <= 0:
        raise ValueError('Invalid artifact name or size')
    lock = FileLock(str(target) + '.lock')
    while True:
        if state := stopped():
            raise DownloadStopped(state)
        try:
            lock.acquire(timeout=.2)
            break
        except Timeout:
            continue
    try:
        if checked_file(root, item):
            report(received=item['bytes'], cached=True, state='verified')
            return target
        deadline = time.monotonic() + 5
        while target.with_suffix('.alive').exists() and time.monotonic() < deadline:
            if state := stopped():
                raise DownloadStopped(state)
            time.sleep(POLL_SECONDS)
        if target.with_suffix('.alive').exists():
            raise ValueError('Previous transfer is still stopping; wait before continuing')
        recover_response(target, partial, metadata, item)
        if str((env or os.environ).get('UV_OFFLINE', '')).lower() in ('1', 'true'):
            if not (partial.exists() and partial.stat().st_size == item['bytes'] and digest(partial) == item['sha256']):
                raise DownloadWaiting('Offline mode: a required artifact is not cached')
        curl = curl_executable()
        waited = 0.0
        hash_failures = 0
        for attempt in range(RETRIES + 1):
            if state := stopped():
                raise DownloadStopped(state)
            if partial.exists() and partial.stat().st_size == item['bytes']:
                if digest(partial) == item['sha256']:
                    os.replace(partial, target)
                    report(received=item['bytes'], state='verified')
                    return target
                hash_failures += 1
                partial.replace(partial.with_suffix('.corrupt'))
                if hash_failures >= 2:
                    raise ValueError('Artifact checksum mismatch; download stopped')
            if partial.exists() and partial.stat().st_size > item['bytes']:
                partial.replace(partial.with_suffix('.corrupt'))
            offset = partial.stat().st_size if partial.exists() else 0
            old = json.loads(metadata.read_text()) if metadata.exists() else {}
            validator = old.get('etag') or old.get('last-modified')
            # Keep each response separate: a server ignoring Range must never corrupt a prefix.
            chunk, head = target.with_suffix('.chunk'), target.with_suffix('.headers')
            command = [curl, '--silent', '--location', '--max-redirs', '5', '--proto', '=http,https' if allow_http else '=https',
                       '--proto-redir', '=http,https' if allow_http else '=https', '--connect-timeout', str(CONNECT_TIMEOUT),
                       '--dump-header', str(head), '--output', str(chunk), '--header', 'Accept-Encoding: identity']
            if offset:
                command += ['--header', f'Range: bytes={offset}-']
                if validator:
                    command += ['--header', f'If-Range: {validator}']
            if bandwidth:
                command += ['--limit-rate', str(max(1, int(bandwidth)))]
            command += [url]
            target.with_suffix('.attempt').write_text(json.dumps({'offset': offset, 'validator': validator}))
            # Never log curl stderr: it can contain proxy credentials or private endpoint details.
            with head.open('wb'), chunk.open('wb'):
                pass
            target.with_suffix('.alive').touch()
            try:
                proc = subprocess.Popen([sys.executable, '-c', CURL_GUARDIAN, str(target.with_suffix('.alive')), *command], env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                target.with_suffix('.alive').unlink(missing_ok=True)
                raise
            last_size, last_data, last_emit = 0, time.monotonic(), 0.0
            timed_out = False
            try:
                while proc.poll() is None:
                    state = stopped()
                    size = chunk.stat().st_size
                    now = time.monotonic()
                    if size != last_size:
                        last_size, last_data = size, now
                    if now - last_emit >= PROGRESS_SECONDS:
                        status, _ = headers(head)
                        report(received=min(item['bytes'], size + (offset if status == 206 else 0)),
                               state='downloading', attempt=attempt, next_retry_at=None)
                        last_emit = now
                    if state or now - last_data > IDLE_TIMEOUT or size > item['bytes']:
                        timed_out = not state
                        proc.stdin.close()
                        break
                    time.sleep(POLL_SECONDS)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            finally:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            status, fields = headers(head)
            size = chunk.stat().st_size
            valid_response = status == 200
            if status == 206:
                import re
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', fields.get('content-range', ''))
                valid_response = bool(match and int(match[1]) == offset and int(match[3]) == item['bytes']
                                      and int(match[2]) < item['bytes'] and size <= int(match[2]) - offset + 1)
                new_validator = fields.get('etag') or fields.get('last-modified')
                if validator and new_validator and validator != new_validator:
                    valid_response = False
                if not valid_response:
                    partial.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
                    report(received=0, state='restarting', reason='Remote file or range changed')
            if valid_response and size <= item['bytes']:
                recover_response(target, partial, metadata, item)
            else:
                chunk.unlink(missing_ok=True)
                head.unlink(missing_ok=True)
                target.with_suffix('.attempt').unlink(missing_ok=True)
            if state := stopped():
                raise DownloadStopped(state)
            if partial.exists() and partial.stat().st_size == item['bytes']:
                if digest(partial) == item['sha256']:
                    os.replace(partial, target)
                    report(received=item['bytes'], state='verified', next_retry_at=None)
                    return target
                hash_failures += 1
                partial.replace(partial.with_suffix('.corrupt'))
                if hash_failures >= 2:
                    raise ValueError('Artifact checksum mismatch; download stopped')
            if status == 416:
                partial.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)
            elif (400 <= status < 500 and status not in (408, 429)) or proc.returncode in (23, 26, 60, 77, 126):
                raise ValueError(f'Download cannot continue (HTTP {status}, transport {proc.returncode}); check access, disk space or certificates')
            delay = retry_delay(fields.get('retry-after'), attempt)
            if attempt == RETRIES or waited + delay > WAIT_BUDGET:
                raise DownloadWaiting('Network retries exhausted. Partial downloads are kept; continue when connectivity recovers.')
            waited += delay
            report(received=partial.stat().st_size if partial.exists() else 0, state='retrying', attempt=attempt + 1,
                   next_retry_at=time.time() + delay, reason='No download progress' if timed_out else f'Connection interrupted (HTTP {status})')
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                if state := stopped():
                    raise DownloadStopped(state)
                time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
        raise DownloadWaiting('Download incomplete')
    finally:
        lock.release()


def download_all(root, items, *, env, preferences, stopped=lambda: None, progress=lambda **_: None,
                 allow_http=False):
    items = list({v['sha256']: v for v in items}.values())
    state = {v['sha256']: {'name': v['name'], 'bytes': v['bytes'], 'received': 0, 'state': 'queued', 'cached': False} for v in items}
    guard = threading.Lock()
    degraded = threading.Event()
    abort = threading.Event()
    started = time.monotonic()
    last_emit = [0.0]
    baseline = [0]
    total = sum(v['bytes'] for v in items)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    needed = sum(v['bytes'] for v in items if not checked_file(root, v))
    # Download storage plus extracted environment and temporary response segment.
    if shutil.disk_usage(root).free < needed + total * 2:
        raise ValueError('Insufficient disk space for downloads and the extracted environment')
    for item in items:
        target, partial, _ = cache_paths(root, item)
        cached = checked_file(root, item)
        state[item['sha256']].update(received=item['bytes'] if cached else min(item['bytes'], partial.stat().st_size) if partial.exists() else 0,
                                   cached=cached, state='verified' if cached else 'queued')
    baseline[0] = sum(v['received'] for v in state.values())

    def emit(key, **fields):
        with guard:
            if fields.get('state') == 'verified':
                fields.update(reason=None, next_retry_at=None)
            state[key].update(fields)
            if fields.get('state') == 'retrying':
                degraded.set()
            now = time.monotonic()
            if now - last_emit[0] < PROGRESS_SECONDS:
                return
            last_emit[0] = now
            received = sum(v['received'] for v in state.values())
            speed = max(0, received - baseline[0]) / max(.001, now - started)
            progress(phase='download', download_total=total, downloaded=received,
                     cached_bytes=sum(v['bytes'] for v in state.values() if v['cached']),
                     speed_bytes_s=round(speed), eta_seconds=round((total - received) / speed) if speed else None,
                     concurrency=1 if degraded.is_set() else preferences['concurrency'],
                     bandwidth_bytes_s=preferences['bandwidth_bytes_s'], files=[dict(v) for v in state.values()])

    def work(item):
        return transfer(root, item, env=env, stopped=lambda: stopped() or ('paused' if abort.is_set() else None),
                        report=lambda **fields: emit(item['sha256'], **fields),
                        bandwidth=preferences['bandwidth_bytes_s'] / preferences['concurrency'], allow_http=allow_http)

    results = {}
    pending_items = iter(items)
    with ThreadPoolExecutor(max_workers=preferences['concurrency']) as executor:
        pending = {}
        try:
            while True:
                capacity = 1 if degraded.is_set() else preferences['concurrency']
                while len(pending) < capacity:
                    item = next(pending_items, None)
                    if item is None:
                        break
                    pending[executor.submit(work, item)] = item
                if not pending:
                    break
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    item = pending.pop(future)
                    results[item['sha256']] = future.result()
        except BaseException:
            abort.set()
            raise
    if items:
        last_emit[0] = 0.0
        emit(items[-1]['sha256'])
    return results
