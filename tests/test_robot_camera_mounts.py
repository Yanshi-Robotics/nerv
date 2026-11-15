"""Camera overlays must remain local sensors and preserve physical dynamics."""
import copy
import threading
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from nerv.platform.registry.schema import SensorDecl
from nerv.world.composition import SharedWorld, compose
from nerv.world.mujoco_node import _body_defaults_from_registry
from nerv.world.robot_camera_mounts import apply_robot_cameras, compile_robot_cameras


XML = '''<mujoco><worldbody><geom name="floor" type="plane" size="4 4 .1"/>
<body name="base" pos="0 0 1"><freejoint/><geom type="box" size=".2 .1 .1" mass="1"/>
<camera name="eye"/><body name="foot" pos="0 0 -.4"><joint name="ankle"/>
<geom type="box" size=".1 .05 .05" mass=".1"/></body></body>
<body name="foreign" pos="3 0 1"><geom type="sphere" size=".1"/></body>
</worldbody><actuator><motor joint="ankle"/></actuator></mujoco>'''
SENSOR = dict(name='camera:ground', camera='ground_down', simulation_mount=dict(
    link='base', position_m=[.3,0,-.1], xyaxes=[0,-1,0,1,0,0], vertical_fov_deg=95))


@pytest.fixture
def arena(tmp_path):
    path = tmp_path/'camera-fixture.xml'
    path.write_text(XML)
    return path


def test_single_overlay_preserves_existing_camera_and_all_dynamics(arena):
    original = mujoco.MjModel.from_xml_path(str(arena))
    overlaid = compile_robot_cameras(str(arena), [SENSOR])
    assert overlaid.ncam == original.ncam + 1
    for field in ('nq','nv','nu','nbody','ngeom','nsensor'):
        assert getattr(overlaid, field) == getattr(original, field)
    for field in ('qpos0','body_mass','body_inertia','dof_armature','geom_pos','geom_aabb','actuator_gear'):
        np.testing.assert_array_equal(getattr(overlaid,field), getattr(original,field))
    old = mujoco.mj_name2id(overlaid,mujoco.mjtObj.mjOBJ_CAMERA,'eye')
    np.testing.assert_array_equal(overlaid.cam_pos[old], original.cam_pos[0])
    down = mujoco.mj_name2id(overlaid,mujoco.mjtObj.mjOBJ_CAMERA,'ground_down')
    assert overlaid.cam_mode[down] == mujoco.mjtCamLight.mjCAMLIGHT_FIXED
    assert overlaid.cam_bodyid[down] == 1
    assert arena.read_text() == XML


@pytest.mark.parametrize('change',[
    {'camera':'eye'}, {'camera':'B/eye'}, {'camera':''}, {'width':0},
    {'simulation_mount':{'link':'world'}}, {'simulation_mount':{'link':'foreign'}},
    {'simulation_mount':{'position_m':[float('nan'),0,0]}},
    {'simulation_mount':{'xyaxes':[0,0,0,1,0,0]}},
    {'simulation_mount':{'xyaxes':[1,0,0,2,0,0]}},
    {'simulation_mount':{'vertical_fov_deg':180}},
    {'simulation_mount':{'target':'foreign'}},
    {'simulation_mount':{'mode':'trackcom'}},
])
def test_invalid_mount_cannot_modify_model_or_select_foreign_camera(arena,change):
    sensor = copy.deepcopy(SENSOR)
    for key,value in change.items():
        if isinstance(value,dict):
            sensor[key].update(value)
        else:
            sensor[key]=value
    spec = mujoco.MjSpec.from_file(str(arena))
    model = spec.compile()
    with pytest.raises(ValueError):
        apply_robot_cameras(spec,model,1,[sensor])
    assert spec.compile().ncam == model.ncam


def test_duplicate_mounts_are_rejected_before_any_insertion(arena):
    spec = mujoco.MjSpec.from_file(str(arena))
    model = spec.compile()
    with pytest.raises(ValueError,match='unique'):
        apply_robot_cameras(spec,model,1,[SENSOR,SENSOR])
    assert spec.compile().ncam == model.ncam


def test_registry_to_shared_profile_preserves_typed_and_serialized_mounts(arena):
    typed = SensorDecl.model_validate(SENSOR)
    for sensors in ([typed],[typed.model_dump(mode='json')]):
        body = SimpleNamespace(actuators={},dir=str(arena.parent),sensors=sensors)
        defaults = _body_defaults_from_registry(body)
        assert defaults['simulation_cameras'][0]['simulation_mount']['link'] == 'base'


def test_shared_camera_names_and_ownership_survive_membership_transaction(arena):
    def slot(name,x):
        return dict(robot_instance_id=name,spawn=dict(x=x,y=0,z=1,yaw=0),
                    body_defaults={'simulation_cameras':[SENSOR]})
    roster=[slot('A',0),slot('B',2)]
    world=SharedWorld(str(arena),roster)
    try:
        for name in ('A','B'):
            view=world.view(name)
            assert set(view.model_cameras()) == {'eye','ground_down'}
            assert view._camera_for('ground_down') == name+'/ground_down'
            with pytest.raises(KeyError):
                view._camera_for(('B' if name=='A' else 'A')+'/ground_down')
        world.transact([roster[1]],expected_revision=1)
        assert world.view('B')._camera_for('ground_down') == 'B/ground_down'
        assert mujoco.mj_name2id(world.model,mujoco.mjtObj.mjOBJ_CAMERA,'A/ground_down') == -1
    finally:
        world.stop()


def test_failed_shared_overlay_cannot_fall_back_to_an_existing_sensor(arena):
    bad=copy.deepcopy(SENSOR)
    bad['simulation_mount']['link']='foreign'
    with pytest.raises(ValueError,match='controlled robot'):
        compose(str(arena),[dict(robot_instance_id='A',spawn=dict(x=0,y=0,z=1,yaw=0),
                                body_defaults={'simulation_cameras':[bad]})])


def test_delayed_render_warmup_keeps_its_own_robot_and_warms_every_camera():
    from nerv.world.shared_node import prepare_rendering
    calls = {'A':[],'B':[]}
    pending = []

    def renderer(label):
        return SimpleNamespace(update_scene=lambda data,*,camera:calls[label].append((data,camera)),
                               render=lambda:None)

    a_renderer,b_renderer = renderer('A'),renderer('B')

    def delayed(fn):
        pending.append(fn)
        raise TimeoutError('Warmup remains queued after timeout')

    def resume(fn):
        pending.pop()(a_renderer)
        fn(b_renderer)

    def view(label,run):
        return SimpleNamespace(data=label,_lock=threading.RLock(),render=SimpleNamespace(run=run),
            model_cameras=lambda:['eye','ground_down'],_camera_for=lambda name:label+'/'+name)

    owner=SimpleNamespace(views={'A':view('A',delayed),'B':view('B',resume)},receipt={})
    states=prepare_rendering(owner)
    assert states['A']['state']=='failed' and states['B']['state']=='ready'
    assert calls['A']==[('A','A/eye'),('A','A/ground_down')]
    assert calls['B']==[('B','B/eye'),('B','B/ground_down')]
