"""Official policy recommendations are a pinned release allowlist, not a Hub scan."""
import re

from .policy_sources import repository, file_name


def entries(manifest):
    catalog = manifest.get('policy_catalog', {'schema': 1, 'policies': []})
    if catalog.get('schema') != 1 or not isinstance(catalog.get('policies'), list):
        raise ValueError('Unsupported official policy catalog')
    seen = set()
    for entry in catalog['policies']:
        repo = repository(entry['repository'])
        if not repo.startswith('yanshi-robotics/') or not re.fullmatch(r'[a-f0-9]{40}', entry['commit']):
            raise ValueError('Official policies require a pinned organization commit')
        if entry['id'] in seen or not entry.get('license') or entry.get('format') not in ('act', 'onnx-motion'):
            raise ValueError('Invalid official policy entry')
        seen.add(entry['id'])
        names = set()
        for item in entry['files']:
            name = file_name(item['path'])
            if name in names or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']) or type(item['bytes']) is not int or item['bytes'] <= 0:
                raise ValueError('Invalid official policy file inventory')
            names.add(name)
        if not names:
            raise ValueError('Official policy has no files')
    return catalog['policies']


def verify_plan(manifest, plan):
    for entry in entries(manifest):
        if (entry['repository'], entry['commit']) == (plan['repository'], plan['commit']):
            expected = {v['path']: (v['sha256'], v['bytes']) for v in entry['files']}
            actual = {v['path']: (v['sha256'], v['bytes']) for v in plan['files']}
            if actual != expected:
                raise ValueError('Hub files differ from the official release inventory')
