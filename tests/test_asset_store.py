import json
import zipfile

import pytest

from nerv.asset_store import AssetStore, MANIFEST, validate


def example(root):
    root.mkdir()
    (root / MANIFEST).write_text(json.dumps({'schema': 1, 'id': 'example', 'name': 'Example',
        'kind': 'scene', 'engine': 'mujoco', 'entry': 'scene.xml', 'license': 'CC0-1.0'}))
    (root / 'scene.xml').write_text('<mujoco><worldbody><geom type="plane" size="1 1 .1"/></worldbody></mujoco>')
    return root


def test_edit_preserves_original_and_frozen_revision_and_export(tmp_path):
    source = example(tmp_path / 'source')
    original = (source / 'scene.xml').read_bytes()
    store = AssetStore(tmp_path / 'home')
    first = store.import_asset(source)
    workspace = store.work / first['id']
    (workspace / 'scene.xml').write_text('<mujoco><worldbody/></mujoco>')
    second = store.save(first['id'])
    assert first['current'] != second['current']
    old, _ = store.resolve(first['id'], first['current'])
    assert (old / 'scene.xml').read_bytes() == original
    assert (source / 'scene.xml').read_bytes() == original
    store.export(first['id'], tmp_path / 'export.zip')
    imported = AssetStore(tmp_path / 'second-home').import_asset(tmp_path / 'export.zip')
    assert imported['current'] == second['current']


def test_saved_revision_tampering_is_detected(tmp_path):
    store = AssetStore(tmp_path / 'home')
    record = store.import_asset(example(tmp_path / 'source'))
    path, _ = store.resolve(record['id'])
    (path / 'scene.xml').write_text('<mujoco/>')
    with pytest.raises(ValueError, match='has changed'):
        store.resolve(record['id'])


@pytest.mark.parametrize('name', ['../outside.xml', '/outside.xml', 'C:/outside.xml', 'a\\b.xml'])
def test_archive_escape_rejected(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(name, '<mujoco/>')
    with pytest.raises(ValueError):
        AssetStore(tmp_path / 'home').import_asset(archive)
    assert not (tmp_path / 'outside.xml').exists()


def test_code_and_xml_extensions_are_rejected(tmp_path):
    source = example(tmp_path / 'source')
    (source / 'scene.xml').write_text('<mujoco><extension><plugin plugin="unsafe"/></extension></mujoco>')
    with pytest.raises(ValueError, match='plugins'):
        validate(source)
    (source / 'scene.xml').write_text('<mujoco/>')
    (source / 'setup.py').write_text('raise Exception()')
    with pytest.raises(ValueError, match='executable'):
        validate(source)


def test_clone_keeps_lineage_and_original(tmp_path):
    store = AssetStore(tmp_path / 'home')
    first = store.import_asset(example(tmp_path / 'source'))
    clone = store.clone(first['id'], 'Personal room')
    assert clone['id'] != first['id']
    assert clone['derived_from']['revision'] == first['current']
    assert clone['name'] == 'Personal room'
    assert store.read(first['id']) == first


def test_official_record_cannot_be_saved_in_place(tmp_path):
    store = AssetStore(tmp_path / 'home')
    record = store.import_asset(example(tmp_path / 'source'))
    record['origin'] = 'official'
    store.record_path(record['id']).write_text(json.dumps(record))
    with pytest.raises(ValueError, match='personal copy'):
        store.save(record['id'])


def test_missing_mesh_and_external_gltf_are_rejected(tmp_path):
    source = example(tmp_path / 'source')
    (source / 'scene.xml').write_text('<mujoco><asset><mesh file="missing.stl"/></asset></mujoco>')
    with pytest.raises(ValueError, match='Missing asset dependency'):
        validate(source)
    (source / 'scene.xml').write_text('<mujoco/>')
    (source / 'view.gltf').write_text(json.dumps({'buffers': [{'uri': 'https://example.com/mesh.bin'}]}))
    with pytest.raises(ValueError, match='portable'):
        validate(source)


def test_export_resolves_parent_symlinks_and_preserves_lineage(tmp_path):
    store = AssetStore(tmp_path / 'home')
    original = store.import_asset(example(tmp_path / 'source'))
    clone = store.clone(original['id'])
    link = tmp_path / 'alias'
    link.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(ValueError, match='outside managed'):
        store.export(clone['id'], link / 'export.zip')
    target = tmp_path / 'export.zip'
    store.export(clone['id'], target)
    with zipfile.ZipFile(target) as bundle:
        manifest = json.loads(bundle.read(MANIFEST))
    assert manifest['derived_from']['revision'] == original['current']
    assert manifest['derived_from']['license'] == 'CC0-1.0'
