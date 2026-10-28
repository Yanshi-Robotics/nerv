"""Resolve exact platform artifacts at release time, never on a student's machine."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tomllib
from urllib.parse import unquote, urlsplit
import urllib.request

from packaging.tags import cpython_tags, compatible_tags, mac_platforms
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
PYTHON = '3.12.13'
UV_VERSION = '0.12.2'
MACOS_MIN = '14.0'  # Required by the pinned ONNX Runtime binary wheels.
TARGETS = {'linux-x86_64': 'x86_64-manylinux_2_28', 'windows-x86_64': 'x86_64-pc-windows-msvc',
           'macos-aarch64': 'aarch64-apple-darwin', 'macos-x86_64': 'x86_64-apple-darwin'}


def tags(target):
    if target == 'linux-x86_64':
        platforms = [f'manylinux_2_{n}_x86_64' for n in range(28, 4, -1)] + ['manylinux2014_x86_64', 'manylinux2010_x86_64', 'manylinux1_x86_64']
    elif target == 'windows-x86_64':
        platforms = ['win_amd64']
    else:
        platforms = list(mac_platforms((14, 0), 'arm64' if target.endswith('aarch64') else 'x86_64'))
    return list(cpython_tags((3, 12), abis=['cp312'], platforms=platforms)) + list(compatible_tags((3, 12), interpreter='cp312', platforms=platforms))


def read_json(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'NERV-release-builder'})
    with urllib.request.urlopen(request, timeout=60) as source:
        return json.load(source)


def python_artifacts(output):
    cached = output / 'python-artifacts.json'
    if cached.exists():
        return json.loads(cached.read_text())
    listings = json.loads(subprocess.check_output(['uv', 'python', 'list', PYTHON, '--only-downloads', '--all-platforms', '--all-arches', '--output-format', 'json']))
    result, releases = {}, {}
    for target in TARGETS:
        system, arch = target.split('-', 1)
        source_os = 'macos' if system == 'macos' else system
        choices = [v for v in listings if v['os'] == source_os and v['arch'] == arch and v['variant'] == 'default' and v['implementation'] == 'cpython' and v.get('url') and (system != 'linux' or v['libc'] == 'gnu')]
        source = choices[0]['url']
        release, name = source.rsplit('/', 2)[-2:]
        name = unquote(name)
        if release not in releases:
            releases[release] = read_json(f'https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{release}')
        asset = next(v for v in releases[release]['assets'] if v['name'] == name)
        checksum = asset.get('digest', '')
        if not checksum.startswith('sha256:'):
            raise ValueError('Python release asset lacks a published SHA-256 digest')
        result[target] = {'name': name, 'url': asset['browser_download_url'], 'bytes': asset['size'], 'sha256': checksum[7:], 'kind': 'python'}
    cached.write_text(json.dumps(result, indent=2))
    return result


def artifact(wheel, output, base_url):
    url = wheel.get('url')
    if url and urlsplit(url).scheme != 'file':
        name = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
        size = wheel.get('size')
        if size is None:
            with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60) as response:
                size = int(response.headers['Content-Length'])
        return {'name': name, 'url': url, 'bytes': size, 'sha256': wheel['hashes']['sha256'], 'kind': 'wheel'}
    if url:
        parsed = urlsplit(url)
        if parsed.netloc not in ('', 'localhost'):
            raise ValueError('Local wheel URL must not reference another host')
        path = Path(urllib.request.url2pathname(parsed.path))
    else:
        path = Path(wheel['path'])
    if not path.is_absolute():
        path = output / path
    return {'name': path.name, 'url': base_url + '/' + path.name, 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'kind': 'wheel'}


def resolve(output, manifest, product, target, backend, python):
    spec = manifest['products'][product]
    lockname = {'host': 'host', 'so101-sim': 'sim', 'lerobot0.6.1': 'real'}[product]
    stem = f'{lockname}-{target}-{backend}'
    constraints = (ROOT / 'release/constraints' / f'{lockname}.txt').read_text()
    if lockname == 'real':
        # CUDA dependencies are a backend property, not a Linux property.
        constraints = '\n'.join(line for line in constraints.splitlines() if not line.startswith(('nvidia-', 'cuda-', 'triton==')))
    constraint_path = output / f'{stem}.constraints'
    constraint_path.write_text(constraints)
    pylock = output / f'pylock.{stem}.toml'
    command = ['uv', 'pip', 'compile', str(output / f'{lockname}.in'), '--format', 'pylock.toml',
               '--python-version', '3.12', '--python-platform', TARGETS[target], '--only-binary', ':all:',
               '--constraint', str(constraint_path), '--find-links', str(output), '--no-header',
               '--output-file', str(pylock)]
    if lockname == 'real' and not target.startswith('macos'):
        command += ['--torch-backend', backend]
    env = {**os.environ, 'UV_CACHE_DIR': str(output / 'uv-cache'), 'UV_CONCURRENT_DOWNLOADS': '4', 'UV_CREDENTIALS_DIR': str(output / 'uv-credentials')}
    if target.startswith('macos'):
        env['MACOSX_DEPLOYMENT_TARGET'] = MACOS_MIN
    with (output / f'{stem}.resolve.log').open('w') as log:
        subprocess.run(command, env=env, stdout=log, stderr=log, check=True, timeout=600)
    priority = {tag: index for index, tag in enumerate(tags(target))}
    items = []
    for package in tomllib.loads(pylock.read_text())['packages']:
        candidates = []
        for wheel in package.get('wheels', []):
            filename = unquote(urlsplit(wheel.get('url', wheel.get('path', ''))).path.rsplit('/', 1)[-1])
            _, _, _, wheel_tags = parse_wheel_filename(filename)
            ranks = [priority[tag] for tag in wheel_tags if tag in priority]
            if ranks:
                candidates.append((min(ranks), wheel))
        if not candidates:
            raise ValueError(f'No compatible binary wheel for {package["name"]}')
        items.append(artifact(min(candidates, key=lambda v: v[0])[1], output, manifest['base_url']))
    # The module's resources are part of the same download total.
    if archive := spec.get('archive'):
        items.append({'name': archive, 'url': manifest['base_url'] + '/' + archive,
                      **manifest['files'][archive], 'kind': 'resource'})
    items.append(python)
    return {'plan_version': 1, 'release_version': manifest['version'], 'target': target, 'backend': backend,
            'available': True, 'validated': False, 'libc_min': '2.28' if target.startswith('linux') else None,
            'macos_min': MACOS_MIN if target.startswith('macos') else None,
            'minimum_driver_major': 580 if backend == 'cu130' else None,
            'minimum_compute_capability': 7.5 if backend == 'cu130' else None,
            'artifacts': items}


def build(output, target_filter=None):
    output = Path(output).resolve()
    manifest_path = output / 'release.json'
    manifest = json.loads(manifest_path.read_text())
    python = python_artifacts(output)
    plans = manifest.setdefault('download_plans', {})
    for product in manifest['products']:
        plans.setdefault(product, {})
        for target in TARGETS:
            if target_filter and target not in target_filter:
                continue
            for backend in (('cpu', 'cu130') if product.startswith('lerobot') and not target.startswith('macos') else ('cpu',)):
                key = target + '/' + backend
                try:
                    plans[product][key] = resolve(output, manifest, product, target, backend, python[target])
                    print(f'{product} {key}: resolved', flush=True)
                except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    plans[product][key] = {'available': False, 'target': target, 'backend': backend,
                        'reason': 'No validated binary dependency plan for this platform. See release build diagnostics.'}
                    print(f'{product} {key}: unavailable ({type(error).__name__})', flush=True)
                manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--target', action='append', choices=list(TARGETS))
    args = parser.parse_args()
    build(args.output, args.target)
