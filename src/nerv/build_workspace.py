"""Create reviewable source snapshots outside the author's checkout.

Only indexed regular files are copied. Worktree edits to those files are preserved;
ignored dependencies, credentials and runtime data never become build inputs.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import uuid

from .install_downloads import digest
from .install_system import atomic_json
from .managed import fingerprint, storage_root

EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.cache',
            '.next', 'out', 'dist', 'build', 'temp', 'memory', 'logs', 'docs',
            '.aws', '.ssh', '.codex', '.claude'}
PRIVATE_NAMES = {'credentials.json', 'trust.json', '.npmrc', '.pypirc', '.netrc'}


def permitted(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and not any(p in EXCLUDED or p in ('..', '') for p in path.parts)
            and path.name not in PRIVATE_NAMES
            and not (path.name.startswith('.env') and path.name != '.env.example'))


def git(root, *arguments):
    return subprocess.run(['git', '-C', str(root), *arguments], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def source_files(root, prefix='', repositories=None):
    repositories = repositories if repositories is not None else {}
    # Record actual checked-out submodule commits, including diverged local work.
    repositories[prefix or '.'] = git(root, 'rev-parse', 'HEAD').decode().strip()
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, _, stage = metadata.decode().split()
        name = os.fsdecode(raw_name)
        relative = (PurePosixPath(prefix) / name).as_posix()
        if not permitted(relative):
            continue
        if stage != '0':
            raise ValueError('Resolve Git conflicts before building: ' + relative)
        source = root / name
        if mode == '160000':
            if not (source / '.git').exists():
                raise ValueError('Initialize the source submodule before building: ' + relative)
            yield from source_files(source, relative, repositories)
        else:
            if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
                raise ValueError('Build inputs cannot be symbolic links: ' + relative)
            if not source.is_file():
                raise ValueError('Indexed build input is missing: ' + relative)
            yield relative, source


def snapshot(source, root=None):
    source = Path(source).expanduser().resolve(strict=True)
    storage = Path(root).resolve() if root else storage_root()
    if storage.is_relative_to(source) or source.is_relative_to(storage):
        raise ValueError('The author checkout and managed storage must be separate')
    if Path(git(source, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != source:
        raise ValueError('Select the root of the NERV source checkout')
    repositories = {}
    inputs = list(source_files(source, repositories=repositories))
    names = [name.casefold() for name, _ in inputs]
    if len(names) != len(set(names)):
        raise ValueError('Build input names collide on case-insensitive systems')
    directory = storage / 'builds' / uuid.uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    status = {'schema': 1, 'state': 'copying', 'repositories': repositories}
    atomic_json(directory / 'build.json', status)
    files = {}
    try:
        for name, path in inputs:
            destination = directory / 'source' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            before = digest(path)
            shutil.copyfile(path, destination, follow_symlinks=False)
            if destination.is_symlink() or digest(destination) != before or digest(path) != before:
                raise ValueError('Build input changed during snapshot: ' + name)
            destination.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
            files[name] = {'sha256': before, 'bytes': destination.stat().st_size}
        status.update(state='ready', files=files, source_fingerprint=fingerprint(files))
        atomic_json(directory / 'build.json', status)
    except BaseException:
        # Keep an inspectable failed transaction; cleanup is explicit.
        status.update(state='failed', files=files)
        atomic_json(directory / 'build.json', status)
        raise
    return directory, status
