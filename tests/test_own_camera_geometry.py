"""Physical invariance and analytic projection, not a navigation success test."""
import json
import math

import mujoco
import numpy as np
import pytest

from nerv.world.own_camera_geometry import capture_geometry, _projection


@pytest.fixture
def fixture():
    model=mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
    <body name="robot" pos="0 0 1"><freejoint/>
    <geom type="box" size=".2 .1 .1" mass="1"/>
    <camera name="down" pos="0 0 .1" xyaxes="0 -1 0 1 0 0" fovy="90"/>
    <body name="foot" pos=".05 .1 -.8"><joint name="ankle" axis="0 1 0"/>
    <geom type="box" size=".15 .05 .1" mass=".1"/></body></body>
    <body name="other" pos="4 0 1"><freejoint/><geom type="sphere" size=".2"/>
    <camera name="other_eye"/></body></worldbody><actuator><motor joint="ankle"/></actuator></mujoco>''')
    data=mujoco.MjData(model)
    mujoco.mj_forward(model,data)
    return model,data


def capture(fixture):
    m,d=fixture
    return capture_geometry(m,d,m.body('robot').id,m.camera('down').id,640,480,['foot'])


def test_analytic_sole_plane_and_foot_polygon_use_camera_coordinates(fixture):
    result=capture(fixture)
    assert result['valid']
    np.testing.assert_allclose(result['projection_plane']['origin_camera_m'],[0,0,-1],atol=1e-12)
    points=np.asarray(result['foot_ground_envelopes']['foot']['points_camera_m'])
    assert sorted(map(tuple,np.round(points,12)))==sorted([
        (-.05,-.1,-1),(-.15,-.1,-1),(-.15,.2,-1),(-.05,.2,-1)])
    assert result['foot_ground_envelopes']['foot']['pixel_projection']['in_frame_projection']==[True]*4
    assert result['foot_ground_envelopes']['foot']['pixel_projection']['occlusion_evaluated'] is False
    assert 'not detected floor' in result['projection_plane']['source']
    assert result['geometry_representation']=='conservative_collision_bounds'
    assert result['projection_plane']['source'].startswith('lowest_declared_support_collision_bound_and_gravity')
    assert result['contact_evaluated'] is False
    assert result['floor_detection_evaluated'] is False
    assert result['occlusion_evaluated'] is False
    json.dumps(result,allow_nan=False)


def test_whole_body_not_only_feet_is_included_in_envelope(fixture):
    result=capture(fixture)
    body=np.array(result['body_ground_envelope']['points_camera_m'])
    foot=np.array(result['foot_ground_envelopes']['foot']['points_camera_m'])
    assert body[:,0].max()==pytest.approx(.1)
    assert foot[:,0].max()==pytest.approx(-.05)
    assert body[:,1].min()==pytest.approx(-.2)


def test_world_translation_and_yaw_cannot_change_own_camera_geometry(fixture):
    model,data=fixture
    original=capture(fixture)
    data.qpos[:7]=[20,-30,5,math.cos(.7),0,0,math.sin(.7)]
    mujoco.mj_forward(model,data)
    moved=capture(fixture)
    assert moved['valid']
    np.testing.assert_allclose(moved['body_bounds_camera_m'],original['body_bounds_camera_m'],atol=1e-12)
    np.testing.assert_allclose(moved['projection_plane']['origin_camera_m'],original['projection_plane']['origin_camera_m'],atol=1e-12)
    for name in ('body_ground_envelope',):
        # Support functions compare polygon shape without assuming vertex ordering.
        directions=np.array([[math.cos(a),math.sin(a)] for a in np.linspace(0,2*math.pi,37)])
        a=np.array(original[name]['points_camera_m'])[:,:2]
        b=np.array(moved[name]['points_camera_m'])[:,:2]
        np.testing.assert_allclose((a@directions.T).max(axis=0),(b@directions.T).max(axis=0),atol=1e-12)
    # Another robot can move without changing the controlled robot's own shape.
    other=model.body('other').jntadr[0]
    data.qpos[model.jnt_qposadr[other]:model.jnt_qposadr[other]+3]=[-100,200,3]
    mujoco.mj_forward(model,data)
    assert capture(fixture)==moved


def test_tilted_base_keeps_envelope_on_measured_gravity_plane(fixture):
    model,data=fixture
    data.qpos[3:7]=[math.cos(.2),0,math.sin(.2),0]
    mujoco.mj_forward(model,data)
    result=capture(fixture)
    assert result['valid']
    up=np.array(result['projection_plane']['up_camera'])
    origin=np.array(result['projection_plane']['origin_camera_m'])
    assert not np.allclose(up,[0,0,1])
    for envelope in [result['body_ground_envelope'],*result['foot_ground_envelopes'].values()]:
        points=np.array(envelope['points_camera_m'])
        np.testing.assert_allclose((points-origin)@up,0,atol=1e-12)


def test_articulation_and_sample_time_are_not_reused_from_previous_capture(fixture):
    model,data=fixture
    original=capture(fixture)
    data.qpos[model.jnt_qposadr[model.joint('ankle').id]]=.6
    data.time=2.5
    mujoco.mj_forward(model,data)
    changed=capture(fixture)
    assert changed['valid'] and changed['sample_sim_time']==2.5
    assert changed['foot_ground_envelopes']!=original['foot_ground_envelopes']


@pytest.mark.parametrize('fault',['support','foreign_support','foreign_camera','gravity','geometry','time'])
def test_invalid_or_foreign_kinematics_never_looks_like_valid_own_geometry(fixture,fault):
    model,data=fixture
    support=['foot']
    camera=model.camera('down').id
    if fault=='support':
        support=[]
    if fault=='foreign_support':
        support=['other']
    if fault=='foreign_camera':
        camera=model.camera('other_eye').id
    if fault=='gravity':
        model.opt.gravity[:]=0
    if fault=='geometry':
        data.geom_xpos[0,0]=float('nan')
    if fault=='time':
        data.time=float('nan')
    result=capture_geometry(model,data,model.body('robot').id,camera,640,480,support)
    assert result['valid'] is False
    json.dumps(result,allow_nan=False)


def test_analytic_pixel_projection_does_not_claim_occlusion_or_behind_camera_visibility(fixture):
    model,_=fixture
    result=_projection(model,model.camera('down').id,np.array([[.2,.1,-1],[0,0,1],[5,0,-1]]),640,480)
    np.testing.assert_allclose(result['points_px'][0],[368,216],atol=1e-12)
    assert result['points_px'][1] is None
    assert result['in_frame_projection']==[True,False,False]
    assert result['occlusion_evaluated'] is False
