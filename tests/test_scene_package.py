"""Asset packaging preserves numeric physics and source ownership."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

pytest.importorskip('mujoco')
spec = importlib.util.spec_from_file_location('package_scene', Path(__file__).parents[1] / 'scripts/package_scene.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def recipe():
    return {'source': 'scene.xml', 'licenses': [{'files': ['*.xml'], 'license': 'MIT', 'notice': 'LICENSE'}],
            'manifest': {'id': 'fixture', 'name': 'Fixture', 'version': '0.1.0', 'kind': 'scene', 'license': 'MIT'}}


def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'LICENSE').write_text('Fixture license notice')
    (root / 'scene.xml').write_text('<mujoco><compiler angle="radian"/><include file="body.xml"/></mujoco>')
    (root / 'body.xml').write_text('<mujocoinclude><worldbody><body><joint name="hinge" range="-1.7453293 1.7453293"/><geom type="sphere" size=".1"/></body></worldbody></mujocoinclude>')
    return root


def test_roundtrip_preserves_exact_joint_text_and_is_reproducible(tmp_path):
    root = source(tmp_path)
    original = (root / 'body.xml').read_bytes()
    first = module.pack(root, recipe(), tmp_path / 'one.zip')
    second = module.pack(root, recipe(), tmp_path / 'two.zip')
    assert first['sha256'] == second['sha256']
    assert first['validation']['compiled_physics'] == 'passed'
    with zipfile.ZipFile(tmp_path / 'one.zip') as bundle:
        xml = bundle.read('scene.xml')
        assert b'1.7453293' in xml
        assert b'<include' not in xml
    assert (root / 'body.xml').read_bytes() == original


def test_include_requires_license_and_cannot_escape(tmp_path):
    root = source(tmp_path)
    config = recipe()
    config['licenses'][0]['files'] = ['scene.xml']
    with pytest.raises(ValueError, match='license declaration'):
        module.pack(root, config, tmp_path / 'package.zip')
    (tmp_path / 'private.xml').write_text('<mujocoinclude/>')
    (root / 'scene.xml').write_text('<mujoco><include file="../private.xml"/></mujoco>')
    with pytest.raises(ValueError, match='escapes'):
        module.pack(root, recipe(), tmp_path / 'package.zip')
    assert not (tmp_path / 'package.zip').exists()


def test_native_plugin_rejected_before_loading(tmp_path):
    root = source(tmp_path)
    (root / 'body.xml').write_text('<mujocoinclude><extension><plugin plugin="unknown"/></extension></mujocoinclude>')
    with pytest.raises(ValueError, match='Native plugins'):
        module.pack(root, recipe(), tmp_path / 'package.zip')


def test_distinct_notices_with_same_license_are_preserved(tmp_path):
    spec = importlib.util.spec_from_file_location('package_scene', Path(__file__).parents[1] / 'scripts/package_scene.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'scene.xml').write_text('<mujoco><include file="body.xml"/></mujoco>')
    (root / 'body.xml').write_text('<mujocoinclude><worldbody><geom type="sphere" size=".1"/></worldbody></mujocoinclude>')
    (root / 'LICENSE-A').write_text('MIT notice from author A')
    (root / 'LICENSE-B').write_text('MIT notice from author B')
    recipe = {'source': 'scene.xml', 'licenses': [
        {'files': ['scene.xml'], 'license': 'MIT', 'notice': 'LICENSE-A'},
        {'files': ['body.xml'], 'license': 'MIT', 'notice': 'LICENSE-B'}],
        'manifest': {'id': 'fixture', 'version': '0.1.0', 'name': 'Fixture', 'kind': 'scene', 'license': 'MIT'}}
    result = module.pack(root, recipe, tmp_path / 'scene.zip')
    assert result['validation']['compiled_physics'] == 'passed'
    with zipfile.ZipFile(result['path']) as archive:
        notices = {archive.read(name).decode() for name in archive.namelist() if name.startswith('licenses/')}
    assert notices == {'MIT notice from author A', 'MIT notice from author B'}
