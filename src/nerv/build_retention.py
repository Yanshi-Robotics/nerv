"""Reviewable compaction of generated build dependencies, never installed data."""
from __future__ import annotations

import json
import os
from contextlib import ExitStack
from pathlib import Path
import shutil
import sys

from filelock import FileLock

from . import managed
from .install_system import atomic_json
from .install_downloads import digest

# Keep packages, source evidence and test reports. Only reproducible dependencies
# and generated source output are reclaimable; release-smoke may contain user data.
GENERATED = ('validation', 'source/frontend/node_modules', 'source/frontend/.next',
             'source/frontend/out', 'source/build', 'source/.cache')


def plan(root):
    root = Path(root).resolve()
    records = []
    active = [Path(p) for p in managed.active_leases(root)]
    references, visited = [], set()
    def collect(value):
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            expanded = os.path.expandvars(os.path.expanduser(value))
            if os.path.isabs(expanded):
                path = Path(expanded)
                references.extend((path.parent.resolve() / path.name, path.resolve()))
    def registry(home):
        import yaml
        for category, filename in (('bodies', 'body.yaml'), ('worlds', 'world.yaml'), ('tools', 'tool.yaml')):
            for file in (Path(home) / category).glob('*/' + filename):
                collect(yaml.safe_load(file.read_text()))
    def pointers(home, recovery=False):
        home = Path(home)
        if recovery and not home.is_dir():
            raise ValueError('A recovery snapshot is missing; repair it before build cleanup')
        if home in visited:
            return
        visited.add(home)
        for pointer in [home / 'active.json', home / 'previous.json', home / 'update-pending.json',
                        *(home / 'runtimes').glob('*.json')]:
            if pointer.is_file():
                value = json.loads(pointer.read_text())
                collect(value)
                if value.get('registry_root'):
                    registry(os.path.expandvars(os.path.expanduser(value['registry_root'])))
                if value.get('snapshot'):
                    pointers(os.path.expandvars(os.path.expanduser(value['snapshot'])), True)
        registry(home)
    for channel in ('stable', 'preview'):
        pointers(managed.channel_home(channel, root))
    for directory in os.environ.get('NERV_REGISTRY_PATHS', '').split(os.pathsep):
        if directory.strip():
            registry(directory)
    from .paths import REGISTRY_ROOT
    registry(REGISTRY_ROOT)
    for marker in (root / 'builds').glob('*/build.json'):
        directory = marker.parent
        if directory.is_symlink() or directory.resolve().parent != root / 'builds':
            continue
        value = json.loads(marker.read_text())
        records.append((marker.stat().st_mtime_ns, directory, value))
    records.sort(reverse=True)
    keep = set()
    for state, count in (('built', 2), ('failed', 1)):
        keep.update(directory for _, directory, value in
                    [r for r in records if r[2].get('state') == state and not r[2].get('compacted') and not r[2].get('compaction')][:count])
    candidates, retained = [], []
    for _, directory, value in records:
        if value.get('compacted'):
            continue
        reasons = []
        if directory in keep:
            reasons.append('recent-build')
        if any(path.is_relative_to(directory) for path in references):
            reasons.append('installed-reference')
        if value.get('state') not in ('built', 'failed'):
            reasons.append('unfinished-build-needs-review')
        if any(p.is_relative_to(directory) or directory.is_relative_to(p) for p in active):
            reasons.append('active-process')
        if any(p.is_relative_to(directory) for p in (Path(sys.prefix).resolve(),
               Path(getattr(sys, '_base_executable', sys.executable)).resolve())):
            reasons.append('current-interpreter')
        paths = [directory / name for name in GENERATED if (directory / name).exists()]
        if any(p.is_symlink() or not p.resolve().is_relative_to(directory) for p in paths):
            reasons.append('linked-directory-needs-review')
        if paths and not reasons:
            files = value.get('files', {})
            if not files or any(not (directory / 'source' / name).is_file()
                                or (directory / 'source' / name).is_symlink()
                                or digest(directory / 'source' / name) != meta.get('sha256')
                                for name, meta in files.items()):
                reasons.append('source-recovery-needs-review')
        row = {'path': str(directory), 'paths': [str(p) for p in paths],
               'record': managed.fingerprint(value), 'reasons': reasons,
               'bytes': sum(p.stat().st_size for d in paths for p in d.rglob('*')
                            if p.is_file() and not p.is_symlink())}
        (retained if reasons else candidates).append(row)
    return {'candidates': candidates, 'retained': retained,
            'bytes': sum(row['bytes'] for row in candidates),
            'confirmation': managed.fingerprint(candidates)}


def compact(root, confirmation=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        for name in ('build-process.lock', 'installation-process.lock'):
            locks.enter_context(FileLock(str(root / name), timeout=0))
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
            raise ValueError('Build references changed; review a fresh build cleanup plan')
        for row in report['candidates']:
            directory = Path(row['path'])
            record = json.loads((directory / 'build.json').read_text())
            # Retain a journal before removing any generated file. An interrupted
            # compaction stays visible and can be retried with a fresh plan.
            record['compaction'] = {'state': 'running', 'paths': row['paths']}
            atomic_json(directory / 'build.json', record)
            for name in row['paths']:
                path = Path(name)
                if path.exists():
                    shutil.rmtree(path)
            record.update(compacted=True, compaction={'state': 'complete', 'paths': row['paths']})
            atomic_json(directory / 'build.json', record)
        return report
