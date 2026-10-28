import hashlib
import json
import zipfile

import pytest

from nerv.build_assets import hydrate


def package(path, name='decor/mesh.obj', payload=b'mesh bytes', checksum=None):
    checksum = checksum or hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(path, 'w') as bundle:
        bundle.writestr('sources.json', json.dumps({name: {'sha256': checksum}}))
        bundle.writestr(f'resources/{checksum}/{name.rsplit("/", 1)[-1]}', payload)
    return path


def test_hydrates_only_missing_data_and_preserves_source_edits(tmp_path):
    archive = package(tmp_path / 'fixture.zip')
    source = tmp_path / 'snapshot'
    assert hydrate(archive, source) == ['decor/mesh.obj']
    mesh = source / 'decor/mesh.obj'
    assert mesh.read_bytes() == b'mesh bytes'
    mesh.write_bytes(b'edited data')
    assert hydrate(archive, source) == []
    assert mesh.read_bytes() == b'edited data'


@pytest.mark.parametrize('name', ['../outside.obj', 'decor/run.py'])
def test_rejects_escape_and_code_fixtures(tmp_path, name):
    with pytest.raises(ValueError):
        hydrate(package(tmp_path / 'fixture.zip', name), tmp_path / 'snapshot')


def test_bad_fixture_hash_is_not_written(tmp_path):
    with pytest.raises(ValueError, match='checksum'):
        hydrate(package(tmp_path / 'fixture.zip', checksum='0' * 64), tmp_path / 'snapshot')
    assert not (tmp_path / 'snapshot/decor/mesh.obj').exists()
