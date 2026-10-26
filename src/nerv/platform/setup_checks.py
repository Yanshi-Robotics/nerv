"""Bounded, operator-started adapter probes, separate from offline management."""
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .. import paths, install_system
from .runtime_management import environment, manage, check_interpreter

LEASE_SECONDS = 25
TIMEOUT_SECONDS = 60


class Checks:
    def __init__(self):
        self.lock = threading.RLock()
        self.current = None
        self.process = None
        self.deadline = 0
        self.cancelled = threading.Event()

    def active(self):
        with self.lock:
            return self.current is not None and self.current['state'] == 'checking'

    def _save(self):
        install_system.atomic_json(Path(paths.NERV_HOME) / 'setup-checks' / (self.current['id'] + '.json'), self.current)

    def start(self, installation, payload):
        with self.lock:
            if self.active():
                raise ValueError('A device check is already running')
            check_interpreter(installation['python'])
            descriptor = manage(installation['python'], installation['provider'], 'describe', {})
            capability = descriptor.get('prerequisites', {})
            if capability.get('version') != 1 or not capability.get('module'):
                raise ValueError('Upgrade this adapter to enable device prerequisite checks')
            manage(installation['python'], installation['provider'], 'invalidate_prerequisites', {})
            self.cancelled = threading.Event()
            self.current = {'id': uuid.uuid4().hex, 'state': 'checking', 'started_at': time.time(),
                            'provider': installation['provider']}
            self.deadline = time.monotonic() + LEASE_SECONDS
            self.process = subprocess.Popen([installation['python'], '-m', capability['module']],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', env=environment())
            self.process.stdin.write(json.dumps(payload) + '\n')
            self.process.stdin.close()
            self.process.stdin = None
            self._save()
            threading.Thread(target=self._watch, daemon=True).start()
            return dict(self.current)

    def _watch(self):
        process = self.process
        started = time.monotonic()
        output = ''
        reason = None
        while True:
            if self.cancelled.is_set():
                reason = 'cancelled'
            elif time.monotonic() > self.deadline:
                reason = 'page_closed'
            elif time.monotonic() - started > TIMEOUT_SECONDS:
                reason = 'timeout'
            if reason:
                process.terminate()
                try:
                    output, _ = process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()
                break
            try:
                output, _ = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        with self.lock:
            try:
                result = json.loads(output.strip().splitlines()[-1]) if not reason and process.returncode == 0 else None
                if not isinstance(result, dict) or result.get('state') not in ('passed', 'unmet', 'unknown'):
                    raise ValueError('invalid result')
                self.current.update(state=result['state'], result=result)
            except (ValueError, IndexError):
                self.current.update(state='unknown', code=reason or 'worker_failed')
            self.current['finished_at'] = time.time()
            self._save()
            self.process = None

    def get(self, identifier, *, renew=False):
        if not identifier.isalnum():
            raise ValueError('Invalid check ID')
        with self.lock:
            if self.current and self.current['id'] == identifier:
                if renew:
                    self.deadline = time.monotonic() + LEASE_SECONDS
                return dict(self.current)
            value = json.loads((Path(paths.NERV_HOME) / 'setup-checks' / (identifier + '.json')).read_text())
            if value['state'] == 'checking':
                value.update(state='unknown', code='interrupted')
            return value

    def cancel(self, identifier=None):
        with self.lock:
            if self.active() and (identifier is None or identifier == self.current['id']):
                self.cancelled.set()
            return {'ok': True}


checks = Checks()
