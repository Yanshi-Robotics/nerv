"""Managed scenes use immutable data and installed simulation capabilities only."""
import json

import pytest

from nerv import paths
from nerv.asset_store import AssetStore, MANIFEST, validate
from nerv.asset_runtime import worlds, world_name
from nerv.platform.registry import Registry
from nerv.platform.registry.schema import BodySpec, BusEndpoint


def scene(root, runtime=None):
    root.mkdir()
    (root / 'scene.xml').write_text('<mujoco><worldbody><geom type="plane" size="1 1 .1"/></worldbody></mujoco>')
    (root / MANIFEST).write_text(json.dumps({'schema': 1, 'id': 'fixture', 'name': 'Room',
        'license': 'CC0-1.0', 'engine': 'mujoco', 'entry': 'scene.xml', 'kind': 'scene',
        'runtime': runtime or {'bodies': ['test-arm']}}))
    return root


def robot(enabled=True):
    return BodySpec(name='test-arm', family='arm', buses={
        'sim': BusEndpoint(kind='zmq', enabled=enabled, python='/managed/sim/python'),
        'real': BusEndpoint(kind='serial', python='/external/real/python')})


def test_revision_binding_survives_edit_and_original_is_unchanged(tmp_path):
    source = scene(tmp_path / 'source')
    original = (source / 'scene.xml').read_bytes()
    store = AssetStore(tmp_path / 'home')
    first = store.import_asset(source)
    registered, errors = worlds(store.home, {'test-arm': robot()})
    assert not errors
    before = registered[world_name(first['id'], first['current'])]
    assert before.python == '/managed/sim/python'
    assert before.kind == 'sim' and before.configuration == {} and before.url == ''
    assert before.supports == {'test-arm': 'scene.xml'}
    (store.work / first['id'] / 'scene.xml').write_text('<mujoco><worldbody/></mujoco>')
    second = store.save(first['id'])
    registered, errors = worlds(store.home, {'test-arm': robot()})
    assert not errors and len(registered) == 2
    assert registered[before.name].asset['current'] is False
    assert (source / 'scene.xml').read_bytes() == original
    assert (store.resolve(first['id'], first['current'])[0] / 'scene.xml').read_bytes() == original
    assert registered[world_name(second['id'], second['current'])].asset['current'] is True


@pytest.mark.parametrize('runtime', [
    {'bodies': ['test-arm'], 'python': '/tmp/python'},
    {'bodies': ['test-arm'], 'configuration': {'builder': {'module': 'evil'}}},
    {'bodies': ['test-arm'], 'spawn': {'source': 'layout', 'scene': 'evil'}},
    {'bodies': ['test-arm'], 'physics': {'render_width': 100000}},
    {'bodies': ['test-arm'], 'spawn': {'initial_joints': {'joint': float('nan')}}},
    {'bodies': ['../../evil']},
])
def test_asset_cannot_supply_executable_settings_or_invalid_values(tmp_path, runtime):
    with pytest.raises(ValueError):
        validate(scene(tmp_path / 'source', runtime))


def test_unavailable_sim_binding_never_uses_real_runtime(tmp_path):
    store = AssetStore(tmp_path / 'home')
    record = store.import_asset(scene(tmp_path / 'source'))
    available, errors = worlds(store.home, {'test-arm': robot(False)})
    assert not available and errors[0]['id'] == record['id']
    assert 'simulation runtime' in errors[0]['reason']


def test_registry_offers_only_current_but_resolves_previous(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(paths, 'NERV_HOME', str(home))
    registry = tmp_path / 'registry'
    body = registry / 'bodies' / 'test-arm'
    body.mkdir(parents=True)
    (body / 'body.yaml').write_text(json.dumps(robot().model_dump()))
    store = AssetStore(home)
    first = store.import_asset(scene(tmp_path / 'source'))
    (store.work / first['id'] / 'scene.xml').write_text('<mujoco><worldbody/></mujoco>')
    second = store.save(first['id'])
    reg = Registry([str(registry)])
    old = world_name(first['id'], first['current'])
    new = world_name(second['id'], second['current'])
    assert reg.world(old).asset['revision'] == first['current']
    assert [w['name'] for w in reg.summary()['worlds']] == [new]
    assert [row['world'] for row in reg.summary()['matrix']] == [new]


def test_corrupt_saved_scene_is_not_offered(tmp_path):
    store = AssetStore(tmp_path / 'home')
    record = store.import_asset(scene(tmp_path / 'source'))
    directory, _ = store.resolve(record['id'])
    (directory / 'scene.xml').write_text('<mujoco/>')
    available, errors = worlds(store.home, {'test-arm': robot()})
    assert not available and 'has changed' in errors[0]['reason']


def test_runtime_check_loads_steps_and_rejects_wrong_joint(tmp_path):
    from nerv.asset_runtime import inspect_scene
    source = scene(tmp_path / 'source', {'bodies': ['test-arm'],
        'spawn': {'initial_joints': {'hinge': 0.1}}})
    (source / 'scene.xml').write_text('''<mujoco><worldbody><body pos="0 0 1">
      <joint name="hinge" range="-90 90"/><geom type="sphere" size=".1"/>
      </body></worldbody><actuator><position joint="hinge" kp="1"/></actuator></mujoco>''')
    result = inspect_scene(source)
    assert result['runtime'] == 'passed' and result['steps'] == 10
    assert result['dimensions']['nu'] == 1
    assert result['rendering'] == result['task'] == 'not_checked'
    manifest = json.loads((source / MANIFEST).read_text())
    manifest['runtime']['spawn']['initial_joints'] = {'missing': 0}
    (source / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='scalar joint'):
        inspect_scene(source)


def test_preview_uses_declared_free_base_pose_without_controller(tmp_path):
    import math
    from nerv.asset_runtime import prepare_scene
    source = scene(tmp_path / 'source', {'bodies': ['test-arm'],
        'spawn': {'x': 3, 'y': -2, 'yaw': math.pi}})
    (source / 'scene.xml').write_text('''<mujoco><worldbody><body pos="0 0 1">
        <freejoint/><geom type="sphere" size=".1"/>
        <body pos="0 0 .2"><joint name="hinge"/><geom type="sphere" size=".1"/></body>
        </body></worldbody><actuator><motor joint="hinge"/></actuator></mujoco>''')
    sim = prepare_scene(source)
    assert sim.data.qpos[:3] == pytest.approx([3, -2, 1])
    assert sim.data.qpos[3:7] == pytest.approx([0, 0, 0, 1])
    assert not sim.spawned


def test_archive_withdraws_new_sessions_but_preserves_history_and_edits(tmp_path):
    store = AssetStore(tmp_path / 'home')
    record = store.import_asset(scene(tmp_path / 'source'))
    workspace = store.work / record['id'] / 'scene.xml'
    workspace.write_text('<mujoco><worldbody/></mujoco>')
    old_path, _ = store.resolve(record['id'], record['current'])
    original = (old_path / 'scene.xml').read_bytes()
    store.remove(record['id'])
    assert store.list() == []
    assert store.list(include_removed=True)[0]['removed']
    registered, errors = worlds(store.home, {'test-arm': robot()})
    assert not errors
    assert registered[world_name(record['id'], record['current'])].asset['current'] is False
    assert (store.resolve(record['id'], record['current'])[0] / 'scene.xml').read_bytes() == original
    assert workspace.read_text() == '<mujoco><worldbody/></mujoco>'
    store.remove(record['id'], restore=True)
    assert len(store.list()) == 1
    registered, errors = worlds(store.home, {'test-arm': robot()})
    assert registered[world_name(record['id'], record['current'])].asset['current'] is True
