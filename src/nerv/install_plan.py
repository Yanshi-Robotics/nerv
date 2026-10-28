"""Resolve a published platform plan without resolving package versions on the client."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

try:
    from . import install_system as systems, install_downloads as downloads
except ImportError:  # Standalone bootstrap embeds these same modules.
    import nerv_install_system as systems
    import nerv_install_downloads as downloads


def select(manifest, product, preferences, facts):
    target = systems.effective_target(preferences, facts)
    plans = manifest.get('download_plans', {}).get(product, {})
    backend = preferences['compute'] if product.startswith('lerobot') else 'cpu'
    cuda = plans.get(target + '/cu130')
    if backend == 'auto':
        backend = 'cu130' if cuda and cuda.get('validated') and systems.gpu_compatible(facts, cuda) else 'cpu'
    plan = plans.get(target + '/' + backend)
    if not plan or not plan.get('available'):
        raise ValueError((plan or {}).get('reason') or 'No compatible download plan. Open System and installation or update the release bundle.')
    if plan.get('macos_min') and tuple(map(int, (facts.get('macos_version') or '0').split('.'))) < tuple(map(int, plan['macos_min'].split('.'))):
        raise ValueError('This installation plan requires macOS ' + plan['macos_min'] + ' or newer')
    if not systems.gpu_compatible(facts, plan):
        raise ValueError('NVIDIA driver or GPU compatibility could not be confirmed. Select CPU or repair the driver.')
    if target.startswith('linux'):
        if facts.get('libc') != 'glibc' or not facts.get('libc_version'):
            raise ValueError('Cannot confirm glibc compatibility; use a supported Linux environment')
        if tuple(map(int, facts['libc_version'].split('.'))) < tuple(map(int, plan.get('libc_min', '2.28').split('.'))):
            raise ValueError('The selected environment requires a newer glibc')
    return plan


def describe(root, manifest, product, facts=None):
    facts = facts or systems.detect()
    preferences = systems.settings(root)
    try:
        plan = select(manifest, product, preferences, facts)
    except ValueError as error:
        return {'compatible': False, 'reason': str(error), 'download_bytes': None}
    items = {v['sha256']: v for v in plan['artifacts']}
    # Display estimate only. Every cached artifact is hashed again before actual installation.
    cached = sum(v['bytes'] for v in items.values() if (p := downloads.cache_paths(root, v)[0]).is_file() and p.stat().st_size == v['bytes'])
    total = sum(v['bytes'] for v in items.values())
    ancestor = Path(root)
    while not ancestor.exists():
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    needed = total - cached + total * 2
    return {'compatible': free >= needed, 'reason': None if free >= needed else 'Insufficient free disk space',
            'target': plan['target'], 'backend': plan['backend'], 'validated': plan.get('validated', False),
            'download_bytes': total, 'cached_bytes': cached, 'remaining_bytes': total - cached,
            'required_free_bytes': needed, 'free_bytes': free,
            'files': [{'name': v['name'], 'bytes': v['bytes']} for v in items.values()]}


def unpack_python(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Unsafe Python archive member')
            bundle.extractall(destination)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter='data')
    matches = [p for p in (destination / 'python/bin/python3', destination / 'python/python.exe') if p.is_file()]
    if len(matches) != 1:
        raise ValueError('Python distribution does not contain the expected interpreter')
    return matches[0]


def runtime_check(python, receipt, *, runner=None):
    """Probe the selected interpreter, not the web browser or host's torch installation."""
    script = '''import json, platform, sys
v={'os':platform.system(),'arch':platform.machine(),'python':list(sys.version_info[:2])}
print(json.dumps(v))'''
    runner = runner or subprocess.run
    result = runner([str(python), '-I', '-c', script], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError('Runtime interpreter check failed')
    observed = json.loads(result.stdout)
    profile = receipt.get('platform')
    if profile:
        system, arch = systems.TARGETS[profile['target']]
        expected_os = {'linux': 'Linux', 'windows': 'Windows', 'macos': 'Darwin'}[system]
        if observed['os'] != expected_os or systems.ARCHES.get(observed['arch'].lower()) != arch or observed['python'] != [3, 12]:
            raise ValueError('Runtime interpreter does not match its installation platform')
    if profile and receipt.get('product', '').startswith('lerobot'):
        probe = runner([str(python), '-I', '-c',
            'import json,torch; print(json.dumps({"torch":torch.__version__,"cuda":torch.version.cuda}))'],
            capture_output=True, text=True, timeout=30)
        if probe.returncode:
            raise ValueError('Runtime tensor library could not be imported')
        observed.update(json.loads(probe.stdout))
        backend = profile['backend']
        if (backend == 'cpu' and profile['target'].startswith(('linux', 'windows')) and observed['cuda'] is not None) or (backend == 'cu130' and observed['cuda'] != '13.0'):
            raise ValueError('Runtime tensor library does not match the selected compute backend')
    return observed


def install_plan(root, plan, source, stage, *, env, preferences, stopped, progress, run, uv, reuse=False):
    root, source, stage = Path(root), Path(source), Path(stage)
    items = plan['artifacts']
    # Local release wheels can be seeded without network access, after hashing.
    for item in items:
        local = source / item['name']
        if not local.is_file():
            local = Path(root) / '.cache/releases' / plan['release_version'] / item['name']
        if local.is_file() and downloads.digest(local) == item['sha256']:
            target, _, _ = downloads.cache_paths(root, item)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                from filelock import FileLock
                with FileLock(str(target) + '.lock'):
                    temporary = target.with_suffix('.seed')
                    shutil.copyfile(local, temporary)
                    os.replace(temporary, target)
    paths = downloads.download_all(root, items, env=env, preferences=preferences, stopped=stopped, progress=progress)
    if state := stopped():
        raise downloads.DownloadStopped(state)
    if reuse:
        return {v['name']: paths[v['sha256']] for v in items}
    progress(phase='verification')
    python_item = next(v for v in items if v.get('kind') == 'python')
    python_root = root / 'python' / python_item['sha256']
    ready = python_root / 'ready.json'
    if ready.exists():
        interpreter = Path(json.loads(ready.read_text())['python'])
    else:
        interpreter = unpack_python(paths[python_item['sha256']], python_root)
        runtime_check(interpreter, {'platform': plan})
        systems.atomic_json(ready, {'python': str(interpreter)})
    progress(phase='environment')
    run([uv, 'venv', '--offline', '--no-python-downloads', '--python', str(interpreter), str(stage)], env, lambda: bool(stopped()), progress)
    wheelhouse = stage / 'wheelhouse'
    wheelhouse.mkdir(parents=True)
    requirements = []
    for item in items:
        if item.get('kind') != 'wheel':
            continue
        destination = wheelhouse / item['name']
        try:
            os.link(paths[item['sha256']], destination)
        except OSError:
            shutil.copyfile(paths[item['sha256']], destination)
        requirements.append(f"{destination.as_uri()} --hash=sha256:{item['sha256']}")
    lock = stage / 'install-requirements.txt'
    lock.write_text('\n'.join(requirements) + '\n', encoding='utf-8')
    python = stage / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    progress(phase='dependencies')
    run([uv, 'pip', 'install', '--offline', '--no-index', '--no-deps', '--no-python-downloads', '--require-hashes',
         '--only-binary=:all:', '--python', str(python), '-r', str(lock)], env, lambda: bool(stopped()), progress)
    runtime_check(python, {'platform': plan})
    return {v['name']: paths[v['sha256']] for v in items}
