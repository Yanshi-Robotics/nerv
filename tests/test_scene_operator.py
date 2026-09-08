"""Actual passive joint forces, lease expiry and camera selection on a small scene."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from nerv.world.scene_operator import SceneOperator


@pytest.fixture
def operator():
    path = Path(__file__).resolve().parents[1] / "worlds/scenes/interaction.py"
    spec = importlib.util.spec_from_file_location("scene_interaction_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = mujoco.MjModel.from_xml_string('''<mujoco><option gravity="0 0 0"/>
      <worldbody><body name="drawer" pos="0 0 1"><joint name="ix_drawer" type="slide" axis="0 -1 0" range="0 .5" damping="1"/>
      <geom name="front" type="box" size=".3 .04 .2" mass="2"/></body>
      <body name="can" pos="1 0 1"><freejoint name="ix_can"/>
      <geom type="sphere" size=".08" mass=".1"/></body></worldbody></mujoco>''')
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    ix = module.Interaction(model, data)
    runtime = SimpleNamespace(interaction=ix, phase="day", phases=["day", "night"],
        catalogue=lambda: {"facilities": [{"id": "drawer", "anchor": [0, 0, 1]}]},
        update_task=lambda: None, task_status=lambda: {"status": "idle"}, reset=ix.cancel)
    sim = SimpleNamespace(model=model, data=data, epoch="current", spawned=True, has_free_base=False,
        phys={"render_width": 640, "render_height": 480}, status=lambda: {"fallen": False},
        physics_alive=lambda: True)
    clock = [0.0]
    instance = SceneOperator(sim, runtime, clock=lambda: clock[0])
    instance.focus("drawer")
    return instance, clock


def test_physical_joint_and_expiry_restore_forces_and_damping(operator):
    control, clock = operator
    lease = control.acquire("s", "tab", "current")
    credentials = dict(session="s", owner="tab", token=lease["token"], epoch="current")
    control.check(**credentials)
    control.command("joint", {"xy": [0, 0], "joint": "ix_drawer", "fraction": 1})
    model, data = control.sim.model, control.sim.data
    baseline = control.runtime.interaction.base_damping.copy()
    for _ in range(100):
        control.tick()
        mujoco.mj_step(model, data)
    assert data.qpos[0] > 0
    assert model.dof_damping[0] > baseline[0]
    clock[0] = 2.01
    control.tick()
    assert not control.active()
    assert not control.runtime.interaction.targets
    assert np.allclose(model.dof_damping, baseline)
    assert np.allclose(data.qfrc_applied, 0)


def test_unknown_owner_or_epoch_cannot_take_control(operator):
    control, _ = operator
    lease = control.acquire("s", "one", "current")
    with pytest.raises(ValueError):
        control.check("s", "other", lease["token"], "current")
    with pytest.raises(ValueError):
        control.check("s", "one", lease["token"], "old")
    with pytest.raises(ValueError):
        control.acquire("other-session", "one", "current")


def test_out_of_reach_or_wrong_part_never_starts_a_joint(operator):
    control, _ = operator
    control.command("view", {"distance": 5})
    with pytest.raises(ValueError):
        control.command("joint", {"xy": [0, 0], "joint": "ix_drawer", "fraction": 1})
    assert not control.runtime.interaction.targets
    control.focus("drawer")
    with pytest.raises(ValueError):
        control.command("joint", {"xy": [0, 0], "joint": "ix_can", "fraction": 1})


def test_reset_and_fall_end_lease_without_changing_object_positions(operator):
    control, _ = operator
    control.acquire("s", "tab", "current")
    before = control.sim.data.qpos.copy()
    control.reset()
    assert not control.active()
    assert np.array_equal(before, control.sim.data.qpos)
    control.acquire("s", "tab", "current")
    control.sim.status = lambda: {"fallen": True}
    control.tick()
    assert not control.active()
    assert control.last_reason == "robot_fallen"
