"""Pinned official data packages, transported by the shared download engine."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from . import install_downloads, install_system, managed
from .asset_store import AssetStore, identifier


def validate_entry(entry):
    if not isinstance(entry, dict) or entry.get('schema') != 1:
        raise ValueError('Unsupported asset catalog entry')
    identifier(entry.get('id'))
    if not isinstance(entry.get('version'), str) or not re.fullmatch(r'\d+\.\d+\.\d+', entry['version']):
        raise ValueError('Asset versions must be explicit release versions')
    for field in ('name', 'license', 'source_commit'):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise ValueError('Missing asset catalog field: ' + field)
    if not re.fullmatch(r'[0-9a-f]{40}', entry['source_commit']):
        raise ValueError('Asset sources must pin a Git commit')
    item = entry.get('archive', {})
    if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip', item['name'])
            or type(item.get('bytes')) is not int or item['bytes'] <= 0
            or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', '')))):
        raise ValueError('Asset archive requires a portable name, size and SHA-256')
    if not isinstance(item.get('url'), str):
        raise ValueError('Asset archive requires an official release URL')
    url = urlsplit(item['url'])
    prefix = '/Yanshi-Robotics/nerv-assets/releases/download/'
    rest = url.path.removeprefix(prefix).split('/')
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
            or not url.path.startswith(prefix) or len(rest) != 2
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', rest[0]) or rest[1] != item['name']):
        raise ValueError('Only pinned official asset release URLs are supported')
    return entry


def entries(manifest):
    catalog = manifest.get('asset_catalog', {'schema': 1, 'assets': []})
    if not isinstance(catalog, dict) or catalog.get('schema') != 1 or not isinstance(catalog.get('assets'), list):
        raise ValueError('Unsupported official asset catalog')
    result = [validate_entry(entry) for entry in catalog['assets']]
    identities = [(entry['id'], entry['version']) for entry in result]
    if len(identities) != len(set(identities)):
        raise ValueError('Duplicate official asset release')
    return result


def install(home, entry, *, env, stopped=lambda: None, progress=lambda **_: None):
    entry = validate_entry(entry)
    root = managed.storage_root(home)
    item = entry['archive']
    progress(phase='download')
    files = install_downloads.download_all(root, [item], env=env,
        preferences=install_system.settings(Path(home)), stopped=stopped, progress=progress)
    reason = stopped()
    if reason:
        raise install_downloads.DownloadStopped(reason)
    progress(phase='verification')
    result = AssetStore(home).install_official(files[item['sha256']], entry)
    progress(state='complete', phase='complete', result=result)
    return result
