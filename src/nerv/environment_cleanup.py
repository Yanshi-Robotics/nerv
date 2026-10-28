"""Explicit, reviewable cleanup of unreferenced managed dependency environments.

User data, assets, external interpreters, caches and build tools are outside this
operation. Retaining an unfamiliar directory is preferable to guessing ownership.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sys
import uuid

from filelock import FileLock

from . import managed


def _size(directory):
    return sum(p.lstat().st_size for p in directory.rglob('*') if not p.is_symlink() and p.is_file())


def plan(root):
    root = Path(root).resolve()
    maintenance = []
    for journal in sorted((root / 'maintenance/cleanup').glob('*.json')):
        record = json.loads(journal.read_text())
        if record.get('state') == 'complete':
            continue
        for item in record.get('items', []):
            if item.get('state') in ('removing', 'failed'):
                path = Path(item['path'])
                if path.exists():
                    maintenance.append({'path': str(path), 'journal': str(journal),
                                        'state': item['state'], 'reason': item.get('reason'),
                                        'action': 'Review the cleanup journal and restore or repair this environment before removing it.'})
    incomplete = {item['path'] for item in maintenance}
    rows = managed.environments(root)
    # venvs may refer to another managed environment's Python installation.
    dependencies = []
    for row in rows:
        cfg = Path(row['path']) / 'pyvenv.cfg'
        if cfg.is_file():
            for line in cfg.read_text().splitlines():
                key, _, value = line.partition('=')
                if key.strip() in ('home', 'executable', 'base-executable') and value.strip():
                    dependencies.append(Path(value.strip()).absolute().resolve())
    candidates, retained = [], []
    for row in rows:
        directory = Path(row['path'])
        reasons = list(row['references'])
        if row['path'] in incomplete:
            reasons.append('incomplete-cleanup-needs-review')
        if row['leased']:
            reasons.append('active-process')
        if Path(sys.prefix).resolve().is_relative_to(directory):
            reasons.append('current-interpreter')
        if any(p.is_relative_to(directory) for p in dependencies):
            reasons.append('python-dependency')
        allowed = (root / 'envs', root / 'profiles/preview/envs')
        if not any(directory.parent.parent == parent for parent in allowed):
            reasons.append('outside-managed-environments')
        receipt = directory / 'nerv-install.json'
        if not row.get('environment_id') or not receipt.is_file():
            reasons.append('legacy-ownership-needs-review')
        item = {'path': str(directory), 'environment_id': row.get('environment_id'),
                'bytes': _size(directory), 'reasons': sorted(set(reasons))}
        if reasons:
            retained.append(item)
        else:
            item['receipt'] = managed.fingerprint(json.loads(receipt.read_text()))
            candidates.append(item)
    return {'candidates': candidates, 'retained': retained, 'maintenance': maintenance,
            'bytes': sum(row['bytes'] for row in candidates),
            'confirmation': managed.fingerprint(candidates)}


def prune(root, confirmation=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Installation/reference switches precede lifecycle locking. New leases
    # cannot be registered between the final reference check and removal.
    with ExitStack() as locks:
        locks.enter_context(FileLock(str(root / 'installation-process.lock'), timeout=0))
        # Old hosts do not participate in shared lifecycle leases. Refuse their
        # coexistence during deletion rather than attempting process discovery.
        if confirmation is not None:
            for channel in ('stable', 'preview'):
                home = managed.channel_home(channel, root)
                if home.exists():
                    locks.enter_context(FileLock(str(home / 'host-running.lock'), timeout=0))
        locks.enter_context(FileLock(str(root / 'lifecycle.lock'), timeout=0))
        report = plan(root)
        if confirmation is None:
            return report
        if confirmation != report['confirmation']:
            raise ValueError('Environment references changed; review a fresh cleanup plan')
        from .install_system import atomic_json
        transaction = root / 'maintenance/cleanup' / (uuid.uuid4().hex + '.json')
        journal = {'state': 'running', 'confirmation': confirmation,
                   'items': [{'path': row['path'], 'state': 'pending'} for row in report['candidates']]}
        atomic_json(transaction, journal)
        removed = []
        for row, item in zip(report['candidates'], journal['items']):
            directory = Path(row['path'])
            if directory.is_symlink():
                raise ValueError('Refusing a linked environment directory')
            item['state'] = 'removing'
            atomic_json(transaction, journal)
            try:
                shutil.rmtree(directory)
            except OSError as error:
                item.update(state='failed', reason=str(error))
                journal['state'] = 'failed'
                atomic_json(transaction, journal)
                return {**report, 'removed': removed, 'failed': str(directory), 'journal': str(transaction)}
            item['state'] = 'removed'
            removed.append(row['path'])
            atomic_json(transaction, journal)
        journal['state'] = 'complete'
        atomic_json(transaction, journal)
        return {**report, 'removed': removed, 'journal': str(transaction)}
