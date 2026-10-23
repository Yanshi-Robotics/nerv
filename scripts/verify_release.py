"""Check local release integrity and installed host recovery. No hardware or API calls.

Usage: python scripts/verify_release.py --manifest temp/release/release.json --home temp/release-smoke
Use a new test home; never point this command at a student's existing ~/.nerv.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--module', action='append', default=[])
    parser.add_argument('--wheelhouse', type=Path, help='Optional predownloaded wheels; release hashes still apply')
    parser.add_argument('--python-cache', type=Path, help='Optional uv managed-Python cache to copy into the test home')
    parser.add_argument('--offline', action='store_true', help='Require cached Python and wheels; prohibit package network requests')
    args = parser.parse_args()
    root = args.home.resolve()
    if root.exists():
        raise SystemExit('Select a new, unused test home')
    root.mkdir(parents=True)
    (root / 'README.md').write_text('Release smoke-test environment. Disposable after acceptance; contains only generated test records.\n')
    os.environ.update(NERV_HOME=str(root), MUJOCO_GL='egl', LIBGL_ALWAYS_SOFTWARE='1', CUDA_VISIBLE_DEVICES='')
    if args.offline:
        os.environ.update(UV_OFFLINE='1', UV_NO_INDEX='1')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from nerv import installation as install
    manifest = args.manifest.resolve()
    data, _ = install.read_manifest(manifest)
    if args.wheelhouse:
        cache = root / '.cache/releases' / data['version']
        cache.mkdir(parents=True)
        for wheel in args.wheelhouse.glob('*.whl'):
            shutil.copy2(wheel, cache / wheel.name)
    if args.python_cache:
        shutil.copytree(args.python_cache, root / 'python', symlinks=True)
    for name, expected in data['files'].items():
        digest = hashlib.sha256((manifest.parent / name).read_bytes()).hexdigest()
        if digest != expected['sha256']:
            raise ValueError(f'Incorrect release asset hash: {name}')
    host = install.install(manifest=manifest)
    for module in args.module:
        install.install(module, manifest=manifest)
    # Imported from a packaged environment, outside the source tree.
    subprocess.run([host['python'], '-m', 'nerv.platform.cli', '--version'], cwd=root, check=True)
    from nerv.platform.control_schemes import Scheme
    fixture = Scheme(id='fixture', name='Recovery verification fixture', robot='fixture',
                     mode='sim', template='fixed_policy', actions=['fixture']).model_dump()
    install.write_json(root / 'schemes/fixture/1.json', fixture)
    install.update(manifest=manifest)
    install.write_json(root / 'schemes/later/1.json', {**fixture, 'id': 'later', 'name': 'Newer recovery fixture'})
    restored = install.rollback()
    assert (root / 'schemes/fixture/1.json').exists()
    assert (Path(restored['preserved_newer_configuration']) / 'schemes/later/1.json').exists()
    assert json.loads((root / 'active.json').read_text())['python'] == host['python']
    print(json.dumps({'ok': True, 'version': data['version'], 'modules': args.module,
                      'installation': 'fresh user directory; not a fresh operating system',
                      'hardware_connected': False}, indent=2))


if __name__ == '__main__':
    main()
