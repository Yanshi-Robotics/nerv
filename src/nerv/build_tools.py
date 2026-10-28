"""Pinned build tools in managed storage, independent of project environments."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

from filelock import FileLock

from . import install_system
from .install_downloads import download_all, digest
from .installation import installer_environment, python_at, uv_executable
from .managed import fingerprint


def build_environment(storage):
    storage = Path(storage)
    temporary = storage / '.cache/build/tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    # npm rejects loading the same path as both user and global configuration.
    # Distinct empty files also work on Windows, where /dev/null is unavailable.
    npm_configs = [storage / '.cache/build' / name for name in ('npm-user.conf', 'npm-global.conf')]
    for path in npm_configs:
        path.touch(exist_ok=True)
        if path.read_bytes():
            raise ValueError('Managed npm configuration must be empty')
    env = installer_environment()
    env.update(NERV_STORAGE_HOME=str(storage), UV_CACHE_DIR=str(storage / '.cache/uv'),
               UV_PYTHON_DOWNLOADS='never', UV_NO_PROGRESS='1', UV_NO_CONFIG='1',
               PIP_CACHE_DIR=str(storage / '.cache/pip'), PIP_CONFIG_FILE=os.devnull,
               PIP_DISABLE_PIP_VERSION_CHECK='1', TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary),
               NPM_CONFIG_CACHE=str(storage / '.cache/npm'), NPM_CONFIG_USERCONFIG=str(npm_configs[0]),
               NPM_CONFIG_GLOBALCONFIG=str(npm_configs[1]), NEXT_TELEMETRY_DISABLED='1',
               NERV_STATIC_EXPORT='1', NEXT_PUBLIC_API='')
    return env


def prepare(source, storage, progress=lambda text: None):
    source, storage = Path(source), Path(storage).resolve()
    manifest = json.loads((source / 'release/build-tools.json').read_text())
    target = install_system.effective_target(install_system.DEFAULTS, install_system.detect())
    item = dict(manifest['node'][target])
    item['url'] = f"https://nodejs.org/dist/v{manifest['node_version']}/{item['name']}"
    lock = source / 'release/build.lock'
    identity = fingerprint({'lock': digest(lock), 'target': target, 'python': '3.12', 'node': item})
    base = storage / 'toolchains/build' / identity
    base.mkdir(parents=True, exist_ok=True)
    env = build_environment(storage)
    with FileLock(str(base / 'prepare.lock'), timeout=0):
        progress('Preparing pinned build tools')
        downloaded = download_all(storage, [item], env=env, preferences=install_system.DEFAULTS)
        node_home = base / 'node'
        name = item['name'].removesuffix('.tar.xz').removesuffix('.tar.gz').removesuffix('.zip')
        node_root = node_home / name
        node = node_root / ('node.exe' if os.name == 'nt' else 'bin/node')
        npm = node_root / ('node_modules/npm/bin/npm-cli.js' if os.name == 'nt' else 'lib/node_modules/npm/bin/npm-cli.js')
        receipt_path = base / 'receipt.json'
        if receipt_path.exists():
            previous = json.loads(receipt_path.read_text())
            if previous.get('identity') != identity:
                raise ValueError('Build tool identity has changed')
            for path, field in ((node, 'node_sha256'), (npm, 'npm_sha256')):
                if not path.is_file() or digest(path) != previous.get(field):
                    raise ValueError('A managed build tool has changed; repair it before building')
        if not node.is_file():
            node_home.mkdir(exist_ok=True)
            archive = downloaded[item['sha256']]
            if zipfile.is_zipfile(archive):
                with zipfile.ZipFile(archive) as bundle:
                    for member in bundle.infolist():
                        if not (node_home / member.filename).resolve().is_relative_to(node_home):
                            raise ValueError('Unsafe build tool archive member')
                    bundle.extractall(node_home)
            else:
                with tarfile.open(archive) as bundle:
                    bundle.extractall(node_home, filter='data')
        if not node.is_file() or not npm.is_file():
            raise ValueError('The Node archive is incomplete')
        actual = subprocess.check_output([str(node), '--version'], text=True, env=env).strip()
        if actual != 'v' + manifest['node_version']:
            raise ValueError('Build Node version does not match the locked version')
        python = python_at(base / 'python')
        if not python.is_file():
            # Bootstrap only from an installed NERV interpreter, never a course venv.
            bootstrap = Path(sys.executable).absolute()
            if not bootstrap.is_relative_to(storage):
                record = json.loads((storage / 'active.json').read_text())
                bootstrap = Path(record['python']).absolute()
            if not bootstrap.parent.parent.resolve().is_relative_to(storage):
                raise ValueError('Install NERV before preparing build tools')
            uv = uv_executable()
            version = subprocess.check_output([uv, '--version'], text=True, env=env).split()[1]
            if version != manifest['uv_version']:
                raise ValueError('Bootstrap uv version differs from the locked build tools')
            subprocess.run([uv, 'venv', '--python', str(bootstrap), str(python.parent.parent)], env=env, check=True)
        version = subprocess.check_output([str(python), '-I', '-c',
            'import sys;print("%d.%d" % sys.version_info[:2])'], text=True, env=env).strip()
        if version != '3.12':
            raise ValueError('The build lock requires a managed Python 3.12 interpreter')
        expected = dict(line.strip().split('==') for line in (source / 'release/build.in').read_text().splitlines()
                        if line.strip() and not line.startswith('#'))
        if not receipt_path.exists():
            uv = uv_executable()
            subprocess.run([uv, 'pip', 'install', '--python', str(python), '--require-hashes',
                            '-r', str(lock)], env=env, check=True)
        # A ready toolchain is immutable, including while another build holds a lease.
        # Repair must create a new environment rather than modifying an active one.
        actual = json.loads(subprocess.check_output([str(python), '-I', '-c',
            'import importlib.metadata as m,json,sys;print(json.dumps({n:m.version(n) for n in json.loads(sys.argv[1])}))',
            json.dumps(list(expected))], text=True, env=env))
        if actual != expected:
            raise ValueError('Managed build dependencies differ from the lock; repair before building')
        tool_bin = python.parent
        env['PATH'] = os.pathsep.join((str(node.parent), str(tool_bin), env.get('PATH', '')))
        record = {'schema': 1, 'identity': identity, 'target': target, 'lock_sha256': digest(lock),
                  'python': str(python), 'node': str(node), 'node_sha256': digest(node),
                  'npm': str(npm), 'npm_sha256': digest(npm)}
        install_system.atomic_json(base / 'receipt.json', record)
    return record, env
