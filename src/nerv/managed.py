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


@contextmanager
def lease(root, paths):
    """A held OS lock is the source of truth, not a potentially recycled PID."""
    from filelock import FileLock
    from .install_system import atomic_json
    directory = Path(root) / 'leases'
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / (uuid.uuid4().hex + '.json')
    with FileLock(str(marker) + '.lock'):
        atomic_json(marker, {'pid': os.getpid(), 'paths': [str(Path(p).resolve()) for p in paths]})
        try:
            yield
        finally:
            marker.unlink(missing_ok=True)
    marker.with_suffix('.json.lock').unlink(missing_ok=True)


def active_leases(root):
    from filelock import FileLock, Timeout
    result = []
    for marker in (Path(root) / 'leases').glob('*.json'):
        try:
            with FileLock(str(marker) + '.lock', timeout=0):
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
                for receipt in (Path(snapshot) / 'runtimes').glob('*.json'):
                    old = json.loads(receipt.read_text())
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
