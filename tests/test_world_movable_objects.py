"""Passive scene bodies never become the controlled robot or its motor targets."""
import threading
import mujoco
import pytest
from nerv.world.mujoco_node import WorldSim, robot_root

PROP = '<body name="prop" pos="2 0 1"><freejoint name="prop_free"/><geom size=".1"/></body>'


def model(*, floating=True, prop_first=True, two_robots=False):
    robot = ('<body name="robot">' + ('<freejoint name="base"/>' if floating else '') +
             '<geom size=".1"/><body name="link"><joint name="motor_joint"/>'
             '<geom size=".1"/></body></body>')
    other = '<body name="other"><joint name="other_joint"/><geom size=".1"/></body>' if two_robots else ''
    actuator = '<motor joint="motor_joint"/>' + ('<motor joint="other_joint"/>' if two_robots else '')
    return mujoco.MjModel.from_xml_string('<mujoco><worldbody>' +
        (PROP+robot if prop_first else robot+PROP) + other + '</worldbody><actuator>' + actuator + '</actuator></mujoco>')


@pytest.mark.parametrize('floating', [True, False])
@pytest.mark.parametrize('prop_first', [True, False])
def test_root_is_actuated_tree(floating, prop_first):
    m = model(floating=floating, prop_first=prop_first)
    root, free = robot_root(m)
    assert m.body(root).name == 'robot'
    assert (m.joint(free).name if free is not None else None) == ('base' if floating else None)


def test_two_actuated_trees_rejected():
    with pytest.raises(ValueError, match='one actuated body tree'):
        robot_root(model(two_robots=True))


def test_passive_prop_cannot_be_spawn_motor():
    sim = WorldSim.__new__(WorldSim)  # No renderer needed for pre-mutation validation.
    sim.model = model()
    sim.base_body, _ = robot_root(sim.model)
    sim._lock = threading.RLock()
    with pytest.raises(ValueError, match='belongs to scenery'):
        sim.spawn({'joint_names': ['prop_free']})


def test_root_free_joint_cannot_be_scalar_motor():
    sim = WorldSim.__new__(WorldSim)
    sim.model = model()
    sim.base_body, _ = robot_root(sim.model)
    with pytest.raises(ValueError, match='one degree of freedom'):
        sim.spawn({'joint_names': ['base']})
