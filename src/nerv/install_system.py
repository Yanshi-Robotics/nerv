"""Host facts and operator installation preferences. No framework or device imports."""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from urllib.parse import urlsplit

DEFAULTS = {"target": "auto", "compute": "auto", "concurrency": 4,
            "bandwidth_bytes_s": 0, "proxy": ""}
ARCHES = {"amd64": "x86_64", "x64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
TARGETS = {"linux-x86_64": ("linux", "x86_64"), "windows-x86_64": ("windows", "x86_64"),
           "macos-aarch64": ("macos", "aarch64"), "macos-x86_64": ("macos", "x86_64")}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def detect():
    system = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(platform.system(), "unknown")
    arch = ARCHES.get(platform.machine().lower(), "unknown")
    translated = False
    if system == 'macos':
        try:
            translated = subprocess.run(['sysctl', '-in', 'sysctl.proc_translated'], capture_output=True,
                                        text=True, timeout=3).stdout.strip() == '1'
        except (OSError, subprocess.TimeoutExpired):
            pass
    gpu = {"status": "tool_missing", "devices": [], "source": "nvidia-smi"}
    executable = shutil.which('nvidia-smi')
    if executable:
        try:
            result = subprocess.run([executable, '--query-gpu=name,driver_version,compute_cap', '--format=csv,noheader,nounits'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode:
                gpu['status'] = 'probe_failed'
            else:
                for line in result.stdout.splitlines():
                    values = [v.strip() for v in line.split(',')]
                    if len(values) == 3:
                        gpu['devices'].append(dict(zip(('name', 'driver', 'compute_capability'), values)))
                gpu['status'] = 'detected' if gpu['devices'] else 'not_found'
        except PermissionError:
            gpu['status'] = 'permission_denied'
        except (OSError, subprocess.TimeoutExpired):
            gpu['status'] = 'probe_failed'
    libc, libc_version = platform.libc_ver() if system == 'linux' else ('', '')
    return {'os': system, 'os_version': platform.release(), 'macos_version': platform.mac_ver()[0] if system == 'macos' else None, 'arch': arch,
            'process_bits': 64 if sys.maxsize > 2**32 else 32,
            'python': platform.python_version(), 'python_abi': sysconfig.get_config_var('SOABI'),
            'libc': libc or ('unknown' if system == 'linux' else None), 'libc_version': libc_version,
            'wsl': system == 'linux' and 'microsoft' in platform.release().lower(),
            'container': Path('/.dockerenv').exists() or Path('/run/.containerenv').exists(),
            'translated': translated, 'physical_arch': 'aarch64' if translated else arch,
            'gpu': gpu, 'checked_at': time.time(), 'source': 'host runtime and nvidia-smi metadata'}


def settings(root, public=False):
    path = Path(root) / 'installation-settings.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    result = {**DEFAULTS, **saved}
    if public:
        result['proxy_configured'] = bool(result.pop('proxy', ''))
    return result


def validate_target(target, facts):
    if target not in TARGETS:
        raise ValueError('Unsupported platform. Select a published target in System and installation.')
    system, arch = TARGETS[target]
    if facts['os'] not in ('unknown', system) or facts['arch'] not in ('unknown', arch):
        raise ValueError('Selected platform conflicts with the detected operating system or process architecture')
    if facts.get('process_bits') != 64:
        raise ValueError('A 64-bit process is required')
    if system == 'linux' and facts.get('libc') not in ('glibc', 'unknown'):
        raise ValueError('This release requires glibc; musl environments are not supported')
    return target


def effective_target(preferences, facts):
    target = preferences['target']
    if target == 'auto':
        target = f"{facts['os']}-{facts['arch']}"
    return validate_target(target, facts)


def save_settings(root, values, facts=None, reset=False):
    from filelock import FileLock
    path = Path(root) / 'installation-settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if set(values) - set(DEFAULTS):
        raise ValueError('Unknown installation setting')
    with FileLock(str(path) + '.lock'):
        result = dict(DEFAULTS) if reset else {**settings(root), **values}
        if result['compute'] not in ('auto', 'cpu', 'cu130'):
            raise ValueError('Select auto, cpu or cu130')
        for key, minimum, maximum in [('concurrency', 1, 4), ('bandwidth_bytes_s', 0, 10**10)]:
            if type(result[key]) is not int or not minimum <= result[key] <= maximum:
                raise ValueError(f'Invalid {key}')
        if result['target'] != 'auto':
            validate_target(result['target'], facts or detect())
        proxy = result['proxy']
        if not isinstance(proxy, str) or '\n' in proxy or '\r' in proxy:
            raise ValueError('Invalid proxy')
        if proxy and (urlsplit(proxy).scheme not in ('http', 'https', 'socks5', 'socks5h') or not urlsplit(proxy).hostname):
            raise ValueError('Proxy must use http, https, socks5 or socks5h')
        atomic_json(path, result)
    return settings(root, public=True)


def gpu_compatible(facts, profile):
    if profile.get('backend') != 'cu130':
        return True
    devices = facts.get('gpu', {}).get('devices', [])
    try:
        return bool(devices) and all(int(d['driver'].split('.')[0]) >= profile['minimum_driver_major']
                                    and float(d['compute_capability']) >= profile['minimum_compute_capability'] for d in devices)
    except (ValueError, KeyError, TypeError):
        return False


def receipt_compatible(receipt, facts):
    profile = receipt.get('platform')
    if not profile:
        return {'compatible': None, 'reason': 'Environment predates platform checks; run an environment check'}
    try:
        validate_target(profile['target'], facts)
        if profile.get('libc_min') and tuple(map(int, facts.get('libc_version', '0').split('.'))) < tuple(map(int, profile['libc_min'].split('.'))):
            raise ValueError('The installed environment requires a newer glibc')
        if profile.get('macos_min') and tuple(map(int, (facts.get('macos_version') or '0').split('.'))) < tuple(map(int, profile['macos_min'].split('.'))):
            raise ValueError('The installed environment requires a newer macOS version')
        if not gpu_compatible(facts, profile):
            raise ValueError('The selected NVIDIA environment is incompatible or the driver could not be checked')
        return {'compatible': True, 'reason': None}
    except (ValueError, KeyError):
        return {'compatible': False, 'reason': 'Installed environment does not match this system or GPU; inspect System and installation'}
