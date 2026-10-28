"""Run candidate CI outside the checkout, using the same managed build service.

The CI-provided Python and uv only create a bootstrap venv under ~/.nerv. All
project installs, tests, caches and build output live in a source snapshot there.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--root', type=Path, default=Path.home() / '.nerv')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    root = args.root.resolve()
    if root.is_relative_to(source):
        raise ValueError('CI storage must be outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, NERV_HOME=str(root), NERV_STORAGE_HOME=str(root),
               UV_CACHE_DIR=str(root / '.cache/uv'), PIP_CACHE_DIR=str(root / '.cache/pip'),
               UV_PYTHON_INSTALL_DIR=str(root / 'python'), UV_NO_CONFIG='1',
               PYTHONDONTWRITEBYTECODE='1')
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(key, None)
    if not args.worker:
        bootstrap = root / 'ci/bootstrap'
        python = bootstrap / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run(['uv', 'venv', '--python', sys.executable, str(bootstrap)], env=env, check=True)
        subprocess.run(['uv', 'pip', 'install', '--python', str(python), '--require-hashes',
                        '-r', str(source / 'release/build.lock')], env=env, check=True)
        env['PATH'] = os.pathsep.join([str(python.parent), env.get('PATH', '')])
        subprocess.run([str(python), str(Path(__file__).resolve()), '--worker', '--root', str(root)], env=env, check=True)
        return
    os.environ.update(env)
    sys.path.insert(0, str(source / 'src'))
    from nerv.build_workspace import build
    result = build(source, root, validate=True, smoke=True)
    artifact = root / 'ci/latest.json'
    artifact.write_text(json.dumps(result, indent=2))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write('bundle=' + result['bundle'] + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
