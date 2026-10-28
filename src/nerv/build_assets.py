"""Restore declared data fixtures from pinned packages into a build snapshot."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from .asset_store import relative
from .install_downloads import download_all
from .install_system import DEFAULTS


def hydrate(archive, destination):
    destination = Path(destination).resolve()
    restored = []
    with zipfile.ZipFile(archive) as bundle:
        sources = json.loads(bundle.read('sources.json'))
        for name, record in sources.items():
            name = relative(name)
            target = destination / name
            if not target.resolve().is_relative_to(destination):
                raise ValueError('Fixture path escapes its source snapshot')
            if target.exists():
                continue  # Keep indexed source, including deliberate developer edits.
            if target.suffix.lower() not in {'.obj', '.stl', '.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('A missing source fixture is not a supported data file')
            content = bundle.read(f'resources/{record["sha256"]}/{target.name}')
            if hashlib.sha256(content).hexdigest() != record['sha256']:
                raise ValueError('Source fixture checksum mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(content)
            restored.append(str(name))
    return restored


def prepare(source, storage, env):
    source = Path(source)
    configuration = json.loads((source / 'release/validation-assets.json').read_text())
    catalog = json.loads((source / 'release/asset-catalog.json').read_text())['assets']
    result = {}
    for fixture in configuration['fixtures']:
        entry = next(item for item in catalog if item['id'] == fixture['asset'])
        item = entry['archive']
        files = download_all(storage, [item], env=env, preferences=DEFAULTS)
        result[entry['id']] = hydrate(files[item['sha256']], source / relative(fixture['destination']))
    return result
