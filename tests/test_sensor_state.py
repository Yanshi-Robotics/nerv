"""Sensor contracts must not disguise missing samples or expose a world pose."""
import json
import math
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from nerv.body.sensor_state import proprioception
from nerv.nerve.world import BusState
from nerv.world.camera_metadata import calibration


def test_proprioception_ignores_global_pose_and_preserves_specific_force():
    state = BusState(1., [.1], [.2], imu_quat=[1, 0, 0, 0], imu_gyro=[0., 0., .3],
                     imu_acc=[0., 0., 9.81], odom_xy=[100., 200.], odom_yaw=1.,
                     extra={'imu_specific_force_sample_sim_time':1.})
    result = proprioception(state, ['joint'], source='simulation_physics_derived')
    state.odom_xy, state.odom_yaw, state.imu_quat = [-400., 700.], -2., [0, 0, 0, 1]
    assert proprioception(state, ['joint'], source='simulation_physics_derived') == result
    assert result['imu_specific_force']['values'] == [0., 0., 9.81]
    assert result['joints']['velocity']['values'] == [.2]
    assert 'odom' not in json.dumps(result) and 'imu_quat' not in json.dumps(result)


@pytest.mark.parametrize('missing', [[], None, [float('nan')], [True], [1., 2.]])
def test_invalid_joint_velocity_is_unavailable_without_fabricated_zero(missing):
    state = BusState(1., [.1], missing)
    result = proprioception(state, ['joint'], source='robot_bus')['joints']['velocity']
    assert result['valid'] is False and 'values' not in result


def test_camera_calibration_is_robot_relative_and_tracks_articulation():
    model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody><camera name="world_eye"/>
      <body name="base"><freejoint/><geom size=".1"/>
      <body name="torso" pos="0 0 .5"><joint name="waist" axis="0 0 1"/>
      <geom size=".1"/><camera name="eye" pos=".1 0 .2" fovy="60"/></body>
      </body></worldbody></mujoco>''')
    data = mujoco.MjData(model)
    base, eye = model.body('base').id, model.camera('eye').id
    mujoco.mj_forward(model, data)
    first = calibration(model, data, base, eye, 640, 480)
    data.qpos[:3] = [100, 200, 20]
    angle = 1.2
    data.qpos[3:7] = [math.cos(angle/2), 0, 0, math.sin(angle/2)]
    mujoco.mj_forward(model, data)
    moved = calibration(model, data, base, eye, 640, 480)
    assert np.allclose(first['transform_base_from_camera'], moved['transform_base_from_camera'])
    assert first['calibration_revision'] == moved['calibration_revision']
    data.qpos[model.joint('waist').qposadr[0]] = .5
    mujoco.mj_forward(model, data)
    rotated = calibration(model, data, base, eye, 640, 480)
    assert not np.allclose(first['transform_base_from_camera'], rotated['transform_base_from_camera'])
    assert first['calibration_revision'] == rotated['calibration_revision']
    assert rotated['vertical_fov_deg'] == 60 and rotated['resolution_px'] == [640, 480]
    assert calibration(model, data, base, model.camera('world_eye').id, 640, 480)['valid'] is False


def test_observation_distinguishes_clear_point_rays_from_body_preflight_blocking():
    from nerv.body.families.humanoid import HumanoidBody
    body = HumanoidBody.__new__(HumanoidBody)
    body.bus = SimpleNamespace(read=lambda:BusState(10., [.1], [.2], odom_yaw=2.))
    body.policy = SimpleNamespace(joint_names=['joint'])
    body.loco = {'brake_margin_m':.35}
    body._heading_reference, body._sensor_source = ('fixture-frame', None, None), 'robot_bus'
    body._held, body._hold_reason, body._last, body._policy_error = False, '', '', ''
    body._clearances, body._front_cone = lambda:{'front':8.}, lambda:8.
    body._fallen, body._stop_snapshot = lambda _:False, lambda:{'stop_confirmed':False}
    body.sensors = []
    body._motion_clearance = lambda kind:('braked', {'valid':True, 'reason':'obstacle',
        'travel_clearance_m':.1, 'sample_sim_time':10.1, 'motion':'forward' if kind=='dist' else 'turn'})
    state, images = body.observe()
    assert state['front_cone_m'] == 8. and not images
    assert state['motion_space']['forward']['inhibit_reason'] == 'braked'
    assert state['motion_space']['forward']['sample_sim_time'] != state['t']
    assert state['heading_reference']['valid'] is False
    assert state['proprioception']['joints']['velocity']['valid'] is True


def test_orthographic_calibration_uses_length_instead_of_angle():
    model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody><body name="base">
      <freejoint/><geom size=".1"/><camera projection="orthographic" fovy="1.5"/>
      </body></worldbody></mujoco>''')
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    result = calibration(model, data, model.body('base').id, 0, 640, 480)
    assert result['vertical_span_m'] == 1.5 and 'vertical_fov_deg' not in result


@pytest.mark.parametrize('mode', ['track', 'trackcom', 'targetbody', 'targetbodycom'])
def test_robot_mounted_camera_cannot_supply_world_or_foreign_target_reference(mode):
    target = 'target="other"' if mode.startswith('target') else ''
    model = mujoco.MjModel.from_xml_string(f'''<mujoco><worldbody>
      <body name="base"><freejoint/><geom size=".1"/>
      <camera name="eye" mode="{mode}" {target}/></body>
      <body name="other" pos="2 0 0"><geom size=".1"/></body>
      </worldbody></mujoco>''')
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    result = calibration(model, data, model.body('base').id, 0, 640, 480)
    assert result == {'schema_revision':1, 'valid':False, 'reason':'camera_mode_not_robot_fixed'}
    assert calibration(model, data, model.body('base').id, -1, 640, 480)['reason'] == 'invalid_camera_id'


def test_unmeasured_simulation_specific_force_is_not_reported_as_a_valid_zero():
    state = BusState(0., [.1], [.2], imu_acc=[0., 0., 0.])
    result = proprioception(state, ['joint'], source='simulation_physics_derived')
    assert result['imu_specific_force']['valid'] is False
    assert 'values' not in result['imu_specific_force']


@pytest.mark.parametrize('sample', [float('nan'), float('inf'), True])
def test_invalid_sample_time_cannot_validate_proprioception(sample):
    state = BusState(sample, [.1], [.2])
    assert not proprioception(state, ['joint'], source='robot_bus')['joints']['position']['valid']


@pytest.mark.parametrize('reset_during_capture', [False, True])
def test_heading_does_not_mix_a_sample_with_a_new_reference(reset_during_capture):
    from nerv.body.families.humanoid import HumanoidBody
    body = HumanoidBody.__new__(HumanoidBody)
    body._heading_reference = ('old', 1., 1.)
    body.bus = SimpleNamespace(read=lambda:BusState(10., [.1], [.2], odom_yaw=2.))
    body.policy = SimpleNamespace(joint_names=['joint'])
    body.loco = {'brake_margin_m':.35}
    body._sensor_source = 'robot_bus'
    body._held, body._hold_reason, body._last, body._policy_error = False, '', '', ''
    def clearances():
        if reset_during_capture:
            body._heading_reference = ('new', 2., 10.)
        return {}
    body._clearances, body._front_cone = clearances, lambda:8.
    body._fallen, body._stop_snapshot = lambda _:False, lambda:{}
    body.sensors = []
    body._motion_clearance = lambda _:('', {})
    state, _ = body.observe()
    assert state['heading_reference']['frame_id'] == 'old'
    assert state['heading_reference']['valid'] is not reset_during_capture
    assert state['heading_deg'] == (None if reset_during_capture else 57.3)
