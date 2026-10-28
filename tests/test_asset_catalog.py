"""Official versions are pinned, immutable and independent of personal work."""
import copy
import json
import zipfile

import pytest

from nerv.asset_catalog import entries, install
from nerv.asset_store import AssetStore, MANIFEST
from nerv.install_downloads import digest


def package(tmp_path, version='1.0.0'):
    archive = tmp_path / (version + '.zip')
    manifest = {'schema': 1, 'id': 'test-room', 'version': version, 'name': 'Room',
        'kind': 'scene', 'engine': 'mujoco', 'entry': 'scene.xml', 'license': 'CC0-1.0'}
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(MANIFEST, json.dumps(manifest))
        z.writestr('scene.xml', '<mujoco><worldbody/></mujoco>')
    entry = {'schema': 1, 'id': manifest['id'], 'version': version, 'name': 'Room',
        'license': 'CC0-1.0', 'source_commit': 'a' * 40,
        'archive': {'name': archive.name, 'sha256': digest(archive), 'bytes': archive.stat().st_size,
            'url': 'https://github.com/Yanshi-Robotics/nerv-assets/releases/download/v1/' + archive.name}}
    return archive, entry


def test_official_update_preserves_personal_copy_and_old_revision(tmp_path):
    archive, entry = package(tmp_path)
    library = AssetStore(tmp_path / 'home')
    first = library.install_official(archive, entry)
    personal = library.clone(first['id'], 'My room')
    personal_path = library.work / personal['id'] / 'scene.xml'
    personal_path.write_text('<mujoco model="personal"/>')
    saved = library.save(personal['id'])
    archive2, entry2 = package(tmp_path, '1.1.0')
    newer = library.install_official(archive2, entry2)
    assert newer['id'] == first['id'] and newer['current'] != first['current']
    assert len(newer['revisions']) == 2
    assert library.resolve(first['id'], first['current'])[0].is_dir()
    assert library.read(personal['id']) == saved
    assert personal_path.read_text() == '<mujoco model="personal"/>'
    with pytest.raises(ValueError, match='personal copy'):
        library.save(first['id'])
    assert library.install_official(archive2, entry2) == newer


def test_failure_does_not_change_current_version(tmp_path):
    archive, entry = package(tmp_path)
    library = AssetStore(tmp_path / 'home')
    first = library.install_official(archive, entry)
    archive2, entry2 = package(tmp_path, '1.1.0')
    entry2['license'] = 'MIT'
    with pytest.raises(ValueError, match='license'):
        library.install_official(archive2, entry2)
    assert library.read(first['id']) == first
    changed = copy.deepcopy(entry)
    changed['archive']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='checksum'):
        library.install_official(archive, changed)


def test_republishing_same_version_rejected(tmp_path):
    archive, entry = package(tmp_path)
    library = AssetStore(tmp_path / 'home')
    library.install_official(archive, entry)
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr('NOTICE', 'Changed release')
    entry['archive'].update(sha256=digest(archive), bytes=archive.stat().st_size)
    with pytest.raises(ValueError, match='published bytes'):
        library.install_official(archive, entry)


@pytest.mark.parametrize('url', [
    'https://github.com/other/assets/releases/download/v1/1.0.0.zip',
    'https://github.com/Yanshi-Robotics/nerv-assets/releases/latest/download/1.0.0.zip',
    'https://github.com@evil.example/Yanshi-Robotics/nerv-assets/releases/download/v1/1.0.0.zip',
    'file:///tmp/1.0.0.zip'])
def test_only_fixed_official_sources(tmp_path, url):
    _, entry = package(tmp_path)
    entry['archive']['url'] = url
    with pytest.raises(ValueError, match='official'):
        entries({'asset_catalog': {'schema': 1, 'assets': [entry]}})


def test_asset_download_uses_shared_engine(tmp_path, monkeypatch):
    archive, entry = package(tmp_path)
    from nerv import install_downloads
    calls = []
    def download(root, items, **kwargs):
        calls.append((root, items, kwargs))
        return {entry['archive']['sha256']: archive}
    monkeypatch.setattr(install_downloads, 'download_all', download)
    monkeypatch.delenv('NERV_STORAGE_HOME', raising=False)
    events = []
    root = tmp_path / 'home'
    record = install(root / 'profiles/preview', entry, env={}, progress=lambda **kw: events.append(kw))
    assert calls[0][0] == root
    assert calls[0][1] == [entry['archive']]
    assert events[-1]['state'] == 'complete'
    assert record['origin'] == 'official'
    assert AssetStore(root).list() == []


def test_queue_freezes_catalog_before_spawn(tmp_path, monkeypatch):
    from nerv.platform import module_jobs
    _, entry = package(tmp_path)
    manifest = {'asset_catalog': {'schema': 1, 'assets': [entry]}}
    monkeypatch.setattr(module_jobs.installation, 'home', lambda: tmp_path / 'home')
    monkeypatch.setattr(module_jobs.installation, 'read_manifest', lambda: (manifest, tmp_path))
    monkeypatch.setattr(module_jobs, 'catalog', lambda: {'jobs': []})
    monkeypatch.setattr(module_jobs, '_spawn', lambda job_id: {'id': job_id})
    job = module_jobs.start_asset(entry['id'], entry['version'])
    path = module_jobs._job_path(job['id'])
    saved = json.loads(path.read_text())
    entry['version'] = '2.0.0'
    assert saved['plan_snapshot']['version'] == '1.0.0'
    calls = []
    monkeypatch.setattr('nerv.asset_catalog.install', lambda home, pinned, **kw: calls.append(pinned))
    module_jobs._work(job['id'])
    assert calls == [saved['plan_snapshot']]


def test_unpublished_asset_cannot_start_job(tmp_path, monkeypatch):
    from nerv.platform import module_jobs
    monkeypatch.setattr(module_jobs.installation, 'home', lambda: tmp_path / 'home')
    monkeypatch.setattr(module_jobs.installation, 'read_manifest', lambda: ({}, tmp_path))
    with pytest.raises(ValueError, match='published version'):
        module_jobs.start_asset('unknown', '1.0.0')
    assert not (tmp_path / 'home').exists()
