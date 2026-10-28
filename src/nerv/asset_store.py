"""Data-only asset workspaces and immutable local revisions.

Importing an asset never executes it or grants control of a robot. Runtime validation
is recorded separately from file validation. Original author directories stay untouched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET

from filelock import FileLock

from .install_downloads import digest
from .install_system import atomic_json
from .managed import fingerprint

MANIFEST = 'nerv-asset.json'
SCHEMA = 1
MAX_FILES = 50000
MAX_BYTES = 20 * 1024 ** 3
DATA_SUFFIXES = {'.json', '.yaml', '.yml', '.xml', '.mjcf', '.obj', '.mtl', '.stl', '.ply',
                 '.png', '.jpg', '.jpeg', '.webp', '.hdr', '.exr', '.glb', '.gltf', '.bin',
                 '.md', '.txt', '.csv', '.msh'}
DATA_NAMES = {'LICENSE', 'NOTICE', 'COPYING'}


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Asset paths must be portable relative paths')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('..', '.') for p in value.split('/')) or '' in value.split('/'):
        raise ValueError('Asset path escapes its package')
    return path


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', value):
        raise ValueError('Invalid local asset identifier')
    return value


def inventory(root):
    files, total, case_names = {}, 0, set()
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():
            raise ValueError('Asset packages cannot contain symbolic links')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Asset packages may only contain regular files')
        name = path.relative_to(root).as_posix()
        relative(name)
        if name.casefold() in case_names:
            raise ValueError('Asset names collide on case-insensitive filesystems')
        case_names.add(name.casefold())
        if path.suffix.lower() not in DATA_SUFFIXES and path.name not in DATA_NAMES:
            raise ValueError('Unsupported or executable asset file: ' + name)
        total += path.stat().st_size
        if len(files) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('Asset package exceeds the import limits')
        files[name] = {'sha256': digest(path), 'bytes': path.stat().st_size}
    return files


def validate(root):
    root = Path(root)
    files = inventory(root)
    if MANIFEST not in files:
        raise ValueError('Missing nerv-asset.json; use the NERV asset template')
    if files[MANIFEST]['bytes'] > 1024 * 1024:
        raise ValueError('Asset manifest exceeds the size limit')
    data = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
    if data.get('schema') != SCHEMA or data.get('kind') not in ('scene', 'robot', 'resources', 'task'):
        raise ValueError('Unsupported asset schema or kind')
    identifier(data.get('id'))
    if not isinstance(data.get('name'), str) or not data['name'].strip() or not data.get('license'):
        raise ValueError('Asset name and license are required')
    if data.get('engine') != 'mujoco':
        raise ValueError('Only the MuJoCo data format is supported by this importer')
    entry = str(relative(data.get('entry')))
    if entry not in files or Path(entry).suffix not in ('.xml', '.mjcf'):
        raise ValueError('The declared MJCF entry is missing')
    # Reject external references and native plugin loading in every XML include.
    trees = {}
    for name in files:
        if Path(name).suffix not in ('.xml', '.mjcf'):
            continue
        raw = (root / name).read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('XML entities and external document types are not permitted')
        tree = ET.fromstring(raw)
        trees[name] = tree
        if any(node.tag in ('plugin', 'extension') for node in tree.iter()):
            raise ValueError('Asset packages cannot load executable plugins')
        for node in tree.iter():
            for key in ('file', 'meshdir', 'texturedir', 'assetdir'):
                value = node.get(key)
                if value:
                    relative(value)
    # MuJoCo resolves compiler directories relative to the main XML. Includes may
    # also have local references. Check package membership now; the engine load
    # remains a separate check and decides the exact resolution semantics.
    directories = {''}
    for tree in trees.values():
        for node in tree.iter('compiler'):
            directories.update(node.get(k) for k in ('meshdir', 'texturedir', 'assetdir') if node.get(k))
    for name, tree in trees.items():
        for node in tree.iter():
            value = node.get('file')
            if not value:
                continue
            bases = {PurePosixPath(name).parent, PurePosixPath(entry).parent}
            prefixes = {''} if node.tag == 'include' else directories
            candidates = {str(base / prefix / value) for base in bases for prefix in prefixes}
            if not candidates.intersection(files):
                raise ValueError('Missing asset dependency: ' + value)
    for name in files:
        if Path(name).suffix.lower() == '.gltf':
            gltf = json.loads((root / name).read_text(encoding='utf-8'))
            for row in gltf.get('buffers', []) + gltf.get('images', []):
                uri = row.get('uri')
                if uri and not uri.startswith('data:'):
                    target = str(PurePosixPath(name).parent / relative(uri))
                    if target not in files:
                        raise ValueError('Missing glTF dependency: ' + uri)
    return {'manifest': data, 'files': files, 'revision': fingerprint(files),
            'validation': {'files': 'passed', 'runtime': 'not_checked', 'task': 'not_checked'}}


def extract(archive, destination):
    total, seen = 0, set()
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist()) > MAX_FILES:
            raise ValueError('Too many archive entries')
        for item in bundle.infolist():
            name = item.filename.rstrip('/')
            relative(name)
            if name.casefold() in seen:
                raise ValueError('Duplicate archive member')
            seen.add(name.casefold())
            mode = item.external_attr >> 16 & 0o170000
            if mode not in (0, 0o100000, 0o040000):
                raise ValueError('Archive links and special files are not permitted')
            total += item.file_size
            if total > MAX_BYTES:
                raise ValueError('Archive exceeds the expanded size limit')
        bundle.extractall(destination)


class AssetStore:
    def __init__(self, home):
        self.home = Path(home)
        self.root = self.home / 'assets'
        self.work = self.home / 'workspaces' / 'assets'

    def record_path(self, key):
        return self.root / 'records' / (identifier(key) + '.json')

    def read(self, key):
        return json.loads(self.record_path(key).read_text(encoding='utf-8'))

    def list(self):
        return [json.loads(p.read_text()) for p in sorted((self.root / 'records').glob('*.json'))]

    def import_asset(self, source):
        source = Path(source).expanduser().resolve(strict=True)
        if source.is_relative_to(self.work.resolve()) or self.work.resolve().is_relative_to(source):
            raise ValueError('Use save for an existing workspace; do not recursively import managed storage')
        self.work.mkdir(parents=True, exist_ok=True)
        key = 'local-' + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(dir=self.work, prefix='.import-') as temp:
            staging = Path(temp) / 'data'
            if source.is_dir():
                inventory(source)
                shutil.copytree(source, staging, symlinks=True)
            elif zipfile.is_zipfile(source):
                staging.mkdir()
                extract(source, staging)
            else:
                raise ValueError('Select an asset directory or ZIP package')
            checked = validate(staging)
            # Validate the copy, not just an author directory that can change during import.
            os.replace(staging, self.work / key)
        record = {'id': key, 'name': checked['manifest']['name'], 'origin': 'local',
                  'workspace': str(self.work / key), 'revisions': [], 'source_asset': checked['manifest']['id']}
        atomic_json(self.record_path(key), record)
        return self.save(key)

    def save(self, key):
        path = self.record_path(key)
        with FileLock(str(path) + '.lock'):
            record = self.read(key)
            if record['origin'] == 'official':
                raise ValueError('Create a personal copy before editing an official asset')
            workspace = self.work / identifier(key)
            parent = self.root / 'versions' / key
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=parent, prefix='.save-') as temp:
                copy = Path(temp) / 'data'
                inventory(workspace)
                shutil.copytree(workspace, copy, symlinks=True)
                checked = validate(copy)
                revision = checked['revision']
                destination = parent / revision
                if not destination.exists():
                    os.replace(copy, destination)
            if revision not in record['revisions']:
                record['revisions'].append(revision)
            record.update(current=revision, name=checked['manifest']['name'], validation=checked['validation'])
            atomic_json(path, record)
            atomic_json(parent / (revision + '.receipt.json'), checked)
            return record

    def install_official(self, archive, entry):
        from .asset_catalog import validate_entry
        entry = validate_entry(entry)
        archive = Path(archive)
        if archive.stat().st_size != entry['archive']['bytes'] or digest(archive) != entry['archive']['sha256']:
            raise ValueError('Official asset checksum mismatch')
        key = 'official-' + identifier(entry['id'])
        identifier(key)
        record_path = self.record_path(key)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(record_path) + '.lock'):
            record = self.read(key) if record_path.exists() else {
                'id': key, 'origin': 'official', 'revisions': [], 'releases': {}}
            if record['origin'] != 'official':
                raise ValueError('Official asset identity conflicts with a local record')
            prior = record.get('releases', {}).get(entry['version'])
            if prior and prior['archive_sha256'] != entry['archive']['sha256']:
                raise ValueError('An official asset release cannot change its published bytes')
            parent = self.root / 'versions' / key
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=parent, prefix='.install-') as temp:
                staging = Path(temp) / 'data'
                staging.mkdir()
                extract(archive, staging)
                checked = validate(staging)
                manifest = checked['manifest']
                if any(manifest.get(k) != entry[k] for k in ('id', 'version', 'license')):
                    raise ValueError('Asset package identity or license differs from its catalog')
                revision = checked['revision']
                destination = parent / revision
                if destination.exists():
                    if validate(destination)['revision'] != revision:
                        raise ValueError('Installed official asset is damaged; restore it before reuse')
                else:
                    os.replace(staging, destination)
            if revision not in record['revisions']:
                record['revisions'].append(revision)
            record.setdefault('releases', {})[entry['version']] = {
                'revision': revision, 'archive_sha256': entry['archive']['sha256'],
                'source_commit': entry['source_commit']}
            record.update(current=revision, version=entry['version'], name=manifest['name'],
                          source_asset=entry['id'], validation=checked['validation'])
            atomic_json(parent / (revision + '.receipt.json'), checked)
            atomic_json(record_path, record)
            return record

    def resolve(self, key, revision=None):
        record = self.read(key)
        revision = revision or record['current']
        if revision not in record['revisions'] or not re.fullmatch(r'[0-9a-f]{64}', revision):
            raise ValueError('Unknown asset revision')
        path = self.root / 'versions' / identifier(key) / revision
        checked = validate(path)
        if checked['revision'] != revision:
            raise ValueError('Saved asset revision has changed; restore or re-import it')
        return path, checked

    def clone(self, key, name=None):
        source, checked = self.resolve(key)
        result = self.import_asset(source)
        result['derived_from'] = {'id': key, 'revision': checked['revision']}
        atomic_json(self.record_path(result['id']), result)
        manifest = self.work / result['id'] / MANIFEST
        data = json.loads(manifest.read_text())
        data['derived_from'] = {**result['derived_from'], 'license': checked['manifest']['license']}
        if name:
            data['name'] = name
        atomic_json(manifest, data)
        result = self.save(result['id'])
        return result

    def export(self, key, destination, revision=None):
        source, checked = self.resolve(key, revision)
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_relative_to(self.root.absolute()) or target.is_relative_to(self.work.absolute()):
            raise ValueError('Choose a new export file outside managed assets')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name in checked['files']:
                bundle.write(source / name, name)
        return {'path': str(target), 'sha256': digest(target), 'revision': checked['revision']}
