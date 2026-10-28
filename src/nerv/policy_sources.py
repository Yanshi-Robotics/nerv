"""Pinned, data-only model sources. Downloading never loads or trusts model code."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import quote, urlsplit

import httpx
from filelock import FileLock

from .install_downloads import download_all, digest
from .install_system import atomic_json
from .managed import fingerprint

API = 'https://huggingface.co'
METADATA_LIMIT = 4 * 1024 * 1024
MAX_FILES = 4096
ALLOWED = {'.onnx', '.safetensors', '.json', '.yaml', '.yml', '.md', '.txt', '.sha256'}


def repository(value):
    if value.startswith('https://huggingface.co/'):
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError('Use a model repository URL and select its revision separately')
        value = parsed.path.strip('/')
    elif value.startswith('hf://'):
        value = value[5:]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*', value):
        raise ValueError('Expected a Hugging Face model ID: organization/model')
    if any(part in ('.', '..') for part in value.split('/')):
        raise ValueError('Invalid model ID')
    return value


def file_name(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Invalid model file path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Model file escapes its snapshot')
    return value


def read_metadata(path, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    url = API + path
    # Metadata and small Git files may redirect through the Hub's resolve cache,
    # but authorization must never be sent to an arbitrary destination.
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for _ in range(6):
            with client.stream('GET', url, headers=headers) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers['location'])
                    parsed = urlsplit(url)
                    if parsed.scheme != 'https' or parsed.netloc != 'huggingface.co':
                        raise ValueError('Unexpected model metadata redirect')
                    continue
                if response.status_code != 200:
                    raise ValueError(f'Model metadata unavailable (HTTP {response.status_code})')
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > METADATA_LIMIT:
                        raise ValueError('Model metadata exceeds the size limit')
                return bytes(content)
    raise ValueError('Too many model metadata redirects')


def resolve(repo, revision='main', token=None):
    """Resolve a moving name once; the returned plan contains immutable URLs only."""
    repo = repository(repo)
    if not revision or len(revision) > 200:
        raise ValueError('Invalid model revision')
    info = json.loads(read_metadata(f'/api/models/{repo}/revision/{quote(revision, safe="")}?blobs=true', token))
    commit = info.get('sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Hub did not return an immutable commit')
    siblings = info.get('siblings', [])
    if not siblings or len(siblings) > MAX_FILES:
        raise ValueError('Invalid model file inventory')
    files, seen = [], set()
    for row in siblings:
        name = file_name(row['rfilename'])
        if name.casefold() in seen:
            raise ValueError('Model paths collide on case-insensitive filesystems')
        seen.add(name.casefold())
        if name == '.gitattributes':
            continue
        if Path(name).suffix.lower() not in ALLOWED and Path(name).name not in ('LICENSE', 'NOTICE', 'COPYING'):
            # An unrelated training script may be in the repository, but it is never
            # downloaded, imported or executed by NERV.
            continue
        url = f'{API}/{repo}/resolve/{commit}/{quote(name, safe="/")}'
        lfs = row.get('lfs')
        if lfs:
            sha, size = lfs.get('sha256'), lfs.get('size')
        else:
            size = row.get('size')
            if type(size) is not int or not 0 < size <= METADATA_LIMIT:
                raise ValueError('Model Git file has no bounded size')
            content = read_metadata(url[len(API):], token)
            blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
            if len(content) != size or blob != row.get('blobId'):
                raise ValueError('Model Git file differs from the pinned inventory')
            sha = hashlib.sha256(content).hexdigest()
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or type(size) is not int or size <= 0:
            raise ValueError('Model file lacks a SHA-256 checksum or size')
        files.append({'path': name, 'name': sha + Path(name).suffix, 'display_name': name,
                      'url': url, 'bytes': size, 'sha256': sha})
    if not any(Path(f['path']).suffix in ('.onnx', '.safetensors') for f in files):
        raise ValueError('No supported ONNX or safetensors weights found')
    return {'schema': 1, 'source': 'huggingface', 'repository': repo, 'commit': commit,
            'private': bool(info.get('private', False)), 'files': files}


@contextmanager
def authorization(home, token):
    if not token:
        yield None
        return
    if any(c in token for c in '\r\n\0'):
        raise ValueError('Invalid model token')
    directory = Path(home) / 'private' / 'transfers'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=directory, prefix='authorization-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write('Authorization: Bearer ' + token + '\n')
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def download(home, plan, *, token=None, stopped=lambda: None, report=lambda **_: None):
    """Keep all model bytes channel-local, including private response fragments."""
    home = Path(home)
    key = fingerprint(plan)
    root = home / 'models' / 'huggingface'
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    with FileLock(str(root / (key + '.lock'))), authorization(home, token) as auth:
        from .installation import installer_environment
        from .install_system import settings
        # The caller's channel owns metadata and private bytes. The transport has
        # the same concurrency, proxy, bandwidth, retries and progress as installs.
        cached_files = download_all(home / 'models', plan['files'],
            env=installer_environment(), preferences=settings(home), stopped=stopped,
            progress=report, authorization_file=auth)
        with tempfile.TemporaryDirectory(dir=root, prefix='.download-') as temporary:
            stage = Path(temporary)
            for item in plan['files']:
                file_name(item['path'])
                cached = cached_files[item['sha256']]
                target = stage / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cached, target)
            if destination.exists():
                if any(not (destination / f['path']).is_file() or digest(destination / f['path']) != f['sha256'] for f in plan['files']):
                    raise ValueError('Installed model snapshot changed; preserve it and import a fresh revision')
            else:
                os.replace(stage, destination)
            atomic_json(root / (key + '.json'), plan)
    return {'path': str(destination), 'source': 'huggingface', 'repository': plan['repository'],
            'commit': plan['commit'], 'fingerprint': key, 'validation': 'download_verified'}
