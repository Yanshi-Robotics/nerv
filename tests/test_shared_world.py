"""Actual CPU physics, using a small generic fixture (not G1 navigation evidence)."""
import numpy as np
import pytest

from nerv.world.composition import SharedWorld
from nerv.world.mujoco_node import robot_root


@pytest.fixture
def world(tmp_path):
    arena = tmp_path / 'arena.xml'
    arena.write_text('''<mujoco><option gravity="0 0 0"/><worldbody>
      <geom type="plane" size="10 10 .1"/>
      <body name="base" pos="0 0 1"><freejoint/><geom type="sphere" size=".2" mass="1"/>
        <camera name="eye"/><body name="foot" pos="0 0 -.3"><joint name="joint"/>
        <geom type="sphere" size=".1" mass="1"/></body></body></worldbody>
      <actuator><motor joint="joint"/></actuator></mujoco>''')
    owner = SharedWorld(str(arena), [dict(robot_instance_id=name, spawn={'x': x, 'y': 0, 'z': 1, 'yaw': 0})
                                   for name, x in [('A', 0), ('B', 2)]])
    yield owner
    owner.stop()


def attach(view):
    return view.spawn({'joint_names': ['joint'], 'kp': [10], 'kd': [1], 'default_pos': [0], 'torque_limit': [100]})


def test_one_clock_separate_actuators_and_no_second_spawn_reset(world):
    a, b = world.view('A'), world.view('B')
    assert a.model is b.model and a.data is b.data
    with pytest.raises(ValueError, match='one actuated'):
        robot_root(a.model)
    attach(a)
    a.data.qpos[a.free_qadr] = .4
    assert not world.step()  # frozen roster: no falling robot while peers attach
    attach(b)
    assert a.data.qpos[a.free_qadr] == .4
    a.write({'targets': [.5]})
    b.write({'targets': [-.5]})
    world.step(10)
    assert a.read()['t'] == b.read()['t'] > 0
    assert a.read()['joint_pos'][0] > 0 > b.read()['joint_pos'][0]
    assert not set(a.qadr) & set(b.qadr)
    assert not set(a.act_id) & set(b.act_id)
    assert a.sensor_names() == b.sensor_names() == ['camera:eye']
    assert a._camera_for('eye') == 'A/eye'
    with pytest.raises(KeyError):
        a._camera_for('B/eye')
    with pytest.raises(ValueError):
        a.reset()
    with pytest.raises(ValueError):
        attach(a)
    with pytest.raises(ValueError):
        world.view(None)


def test_other_robot_is_visible_to_physical_rays(world):
    a, b = world.view('A'), world.view('B')
    attach(a)
    attach(b)
    hit = a._ray(np.array([0., 0., 1.]), np.array([1., 0., 0.]))
    assert hit == pytest.approx(1.8)
    # Own body is excluded, including children, without excluding another robot.
    assert a._ray(np.array([0., 0., 1.]), np.array([-1., 0., 0.])) == -1
