"""General computer readiness, independent of device models and joint definitions."""
import json
import os
import shutil
import time
from pathlib import Path
from .. import installation
from . import module_jobs, runtime_management


def report(product, refresh=False):
    checks = []
    space_ok = False
    def add(key, state, code, evidence=None, action=None):
        checks.append(dict(key=key, state=state, code=code, evidence=evidence, action=action))
    root = installation.home()
    try:
        system = module_jobs.system_description(refresh)
        facts = system['facts']
        plan = system['plans'].get(product, {})
        add('system', 'passed' if plan.get('compatible') else 'unmet',
            'system_compatible' if plan.get('compatible') else 'system_requires_attention',
            {'facts': facts, 'target': plan.get('target'), 'reason': plan.get('reason')}, 'system_settings')
        space_known = 'free_bytes' in plan and 'required_free_bytes' in plan
        space_ok = space_known and plan['free_bytes'] >= plan['required_free_bytes']
        add('disk', ('passed' if space_ok else 'unmet') if space_known else 'unknown',
            'disk_space', {k: v for k, v in plan.items() if k in ('free_bytes', 'required_free_bytes', 'download_bytes', 'cached_bytes')}, 'system_settings')
    except (ValueError, OSError, KeyError) as error:
        plan = {}
        add('system', 'unknown', 'system_requires_attention', str(error), 'system_settings')
    tool = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
    add('download_tool', 'passed' if tool else 'unmet', 'download_tool', tool, 'system_settings')
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    writable = os.access(ancestor, os.W_OK)
    add('permissions', 'passed' if writable else 'unmet', 'installation_permissions', str(ancestor), 'system_settings')
    installed = next((v for v in runtime_management.installations() if v['provider'] == product), None)
    device = None
    if installed:
        try:
            runtime_management.check_interpreter(installed['python'])
            receipt = json.loads((Path(installed['python']).parent.parent / 'nerv-install.json').read_text())
            backend = receipt.get('platform', {}).get('backend')
            if backend not in ('cpu', 'cu130') or module_jobs._needs_repair(product, {product: installed}):
                raise ValueError('Environment receipt needs repair')
            descriptor = runtime_management.manage(installed['python'], installed['provider'], 'describe', {})
            if descriptor.get('prerequisites', {}).get('version') != 1:
                raise ValueError('Upgrade this adapter to enable prerequisite checks')
            device = 'cuda' if backend == 'cu130' else 'cpu'
            add('environment', 'passed', 'environment_ready', {'backend': backend, 'python': installed['python']})
        except (ValueError, OSError, KeyError) as error:
            add('environment', 'unmet', 'environment_repair', str(error), 'install')
    else:
        add('environment', 'unmet', 'environment_missing', action='install')
    return {'version': 1, 'checked_at': time.time(), 'source': 'host', 'checks': checks,
            'plan': plan, 'installation_root': str(root), 'inference_device': device,
            'can_install': bool(plan.get('compatible')) and bool(tool) and writable and space_ok,
            'environment_ready': device is not None}
