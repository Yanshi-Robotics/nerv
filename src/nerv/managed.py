"""Managed storage identities and process leases, independent of robot providers."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import uuid


def storage_root(data_home=None):
    data = Path(data_home or os.environ.get('NERV_HOME', Path.home() / '.nerv')).expanduser().resolve()
    explicit = os.environ.get('NERV_STORAGE_HOME')
    if explicit:
        return Path(explicit).expanduser().resolve()
    if data.name == 'preview' and data.parent.name == 'profiles':
        return data.parent.parent
    return data


def channel_home(channel, root=None):
    if channel not in ('stable', 'preview'):
        raise ValueError('Unknown NERV channel')
    base = Path(root) if root else storage_root()
    return base if channel == 'stable' else base / 'profiles' / 'preview'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def environment_identity(product, spec, files, plan=None):
    """URLs, release labels and UI metadata do not change an environment's identity."""
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', product):
        raise ValueError('Invalid product identifier')
    if plan:
        artifacts = sorted((v['name'], v['sha256']) for v in plan['artifacts'] if v.get('kind') in ('wheel', 'python'))
        target = {k: plan.get(k) for k in ('target', 'backend', 'libc_min', 'macos_min')}
    else:
        artifacts = sorted((n, files[n]['sha256']) for n in spec['packages'])
        # Legacy online resolution cannot be safely reused without a lock.
        target = {'os': platform.system(), 'arch': platform.machine(), 'python': '3.12'}
        if spec.get('lock'):
            artifacts.append((spec['lock'], files[spec['lock']]['sha256']))
        else:
            target['unlocked_transaction'] = uuid.uuid4().hex
    return fingerprint({'schema': 1, 'product': product, 'artifacts': artifacts,
                        'target': target, 'extras': spec.get('extras', {}),
                        'constraints': spec.get('constraints', [])})


def process_alive(pid):
    """Conservative liveness: an inaccessible or reused PID prevents cleanup."""
    if type(pid) is not int or pid <= 0:
        raise ValueError('Invalid managed process identity')
    if platform.system() == 'Linux':
        try:
            # An exited child may await reaping by a different parent. It no
            # longer reads an environment, even though kill(pid, 0) succeeds.
            state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            if state in ('Z', 'X'):
                return False
        except (OSError, IndexError):
            pass
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID; access denied remains protected.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ProcessReservation:
    def __init__(self, marker, record):
        self.marker, self.record = marker, record

    def started(self, pid):
        from .install_system import atomic_json
        if type(pid) is not int or pid <= 0:
            raise ValueError('Invalid managed process identity')
        self.record.update(child_pid=pid, process_state='running')
        atomic_json(self.marker, self.record)

    def failed_to_start(self):
        from .install_system import atomic_json
        self.record['process_state'] = 'not_started'
        atomic_json(self.marker, self.record)


@contextmanager
def lease(root, paths, *, child=False):
    """A held OS lock is the source of truth, not a potentially recycled PID."""
    from filelock import FileLock
    from .install_system import atomic_json
    directory = Path(root) / 'leases'
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / (uuid.uuid4().hex + '.json')
    with FileLock(str(marker) + '.lock'):
        record = {'pid': os.getpid(), 'paths': [str(Path(p).resolve()) for p in paths]}
        if child:
            record['process_state'] = 'starting'
        atomic_json(marker, record)
        try:
            yield ProcessReservation(marker, record)
        finally:
            # A crashed parent does not prove its child stopped. Preserve intent
            # if interrupted between spawn and recording the child's PID.
            retained = record.get('process_state') == 'starting' or (
                record.get('child_pid') and process_alive(record['child_pid']))
            if not retained:
                marker.unlink(missing_ok=True)
    marker.with_suffix('.json.lock').unlink(missing_ok=True)


def active_leases(root):
    from filelock import FileLock, Timeout
    result = []
    for marker in (Path(root) / 'leases').glob('*.json'):
        try:
            with FileLock(str(marker) + '.lock', timeout=0):
                record = json.loads(marker.read_text())
                if record.get('process_state') == 'starting':
                    raise ValueError('An interrupted process launch needs review before cleanup')
                if record.get('child_pid') and process_alive(record['child_pid']):
                    result.extend(record['paths'])
                continue
        except Timeout:
            try:
                result.extend(json.loads(marker.read_text())['paths'])
            except (OSError, ValueError, KeyError):
                raise ValueError('Cannot inspect an active lease; cleanup is unavailable')
    return result


def environments(root=None):
    base = Path(root) if root else storage_root()
    used = set(active_leases(base))
    references = {}
    for channel in ('stable', 'preview'):
        home = channel_home(channel, base)
        pointers = [home / 'active.json', home / 'previous.json', home / 'update-pending.json',
                    *(home / 'runtimes').glob('*.json')]
        for pointer in pointers:
            if not pointer.is_file():
                continue
            record = json.loads(pointer.read_text())
            if record.get('python'):
                directory = str(Path(record['python']).parent.parent.resolve())
                references.setdefault(directory, []).append(channel + ':' + pointer.stem)
            snapshot = record.get('snapshot')
            if snapshot:
                recovery = Path(snapshot)
                for receipt in [recovery / 'active.json', recovery / 'previous.json',
                                *(recovery / 'runtimes').glob('*.json')]:
                    if not receipt.is_file():
                        continue
                    old = json.loads(receipt.read_text())
                    if old.get('python'):
                        directory = str(Path(old['python']).parent.parent.resolve())
                        references.setdefault(directory, []).append(channel + ':rollback')
    rows = []
    receipts = set((base / 'envs').glob('*/*/nerv-install.json'))
    # Pre-unification preview environments remain visible until explicitly migrated.
    receipts.update((channel_home('preview', base) / 'envs').glob('*/*/nerv-install.json'))
    for receipt in sorted(receipts):
        record = json.loads(receipt.read_text())
        directory = str(receipt.parent.resolve())
        rows.append({**record, 'path': directory, 'references': references.get(directory, []),
                     'leased': any(Path(p).is_relative_to(directory) or Path(directory).is_relative_to(p) for p in used),
                     'healthy': None if Path(record.get('python', '')).is_file() else False,
                     'health': 'needs_check' if Path(record.get('python', '')).is_file() else 'missing'})
    return rows
