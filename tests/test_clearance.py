"""Actual MuJoCo rays on physical geometry, with no renderer or listening port."""
import threading
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from nerv.world.clearance import ClearanceScanner
from nerv.world.mujoco_node import WorldSim, make_bus_handler

QUERY = dict(motion="forward", horizon_m=1.0, sample_spacing_m=.08, height_spacing_m=.04,
             envelope_margin_m=.04, max_drop_m=.08, max_rise_m=.08,
             support_probe_margin_m=.05, height_offsets_m=[.08, .28, .60, .95, 1.2],
             support_seam_tolerance_m=.002,
             support_body_names=["foot"])
PLANE = '<geom type="plane" size="10 10 .1"/>'


def scene(scenery=PLANE):
    model = mujoco.MjModel.from_xml_string(f'''<mujoco><worldbody>{scenery}
      <body name="robot" pos="0 0 .5"><freejoint/>
        <geom type="box" size=".16 .2 .3"/>
        <body name="foot" pos=".04 0 -.45"><geom type="box" size=".2 .12 .05"/></body>
        <body name="visual"><geom type="sphere" size="2" contype="0" conaffinity="0"/></body>
      </body></worldbody></mujoco>''')
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data, ClearanceScanner(model, model.body("robot").id)


def test_flat_ground_and_self_visual_exclusion():
    model, data, scanner = scene()
    groups = model.geom_group.copy()
    rgba = model.geom_rgba.copy()
    for motion in ("forward", "turn"):
        result = scanner.measure(data, dict(QUERY, motion=motion))
        assert result["valid"] and result["reason"] == "clear", result
        assert result["turn_clear"]
    assert np.array_equal(model.geom_group, groups)
    assert np.array_equal(model.geom_rgba, rgba)


@pytest.mark.parametrize("height", [.08, .28, .60, .95, 1.2])
def test_obstacles_at_multiple_heights_including_transparent_colliders(height):
    _, data, scanner = scene(PLANE + f'<geom type="box" pos=".7 0 {height}" '
                            'size=".03 .3 .04" rgba="1 1 1 0"/>')
    result = scanner.measure(data, QUERY)
    assert result["valid"] and result["reason"] in ("obstacle", "rise"), result
    assert 0 < result["travel_clearance_m"] <= .7-.03-.24-.04


def test_offset_obstacle_not_on_centre_ray():
    _, data, scanner = scene(PLANE + '<geom type="box" pos=".7 .21 .6" size=".03 .05 .04"/>')
    assert scanner.measure(data, QUERY)["reason"] == "obstacle"


def test_thin_tabletop_between_named_height_levels():
    _, data, scanner = scene(PLANE + '<geom type="box" pos=".7 0 .75" size=".1 .5 .025"/>')
    assert scanner.measure(data, QUERY)["reason"] == "obstacle"


@pytest.mark.parametrize("gap, expected", [(.001, "clear"), (.02, "drop")])
def test_only_tiny_seams_with_four_supported_neighbours_are_tolerated(gap, expected):
    # x=0 is a sampled point. Both sides support a millimetre seam; a real 2 cm
    # slot remains a drop even with the seam-tolerance probe enabled.
    _, data, scanner = scene(f'''<geom type="box" pos="{-2-gap/2} 0 -.1" size="2 3 .1"/>
        <geom type="box" pos="{2+gap/2} 0 -.1" size="2 3 .1"/>''')
    assert scanner.measure(data, QUERY)["reason"] == expected


def test_visual_water_does_not_support_body_but_pool_bottom_does_collide():
    # Floor ends at x=.6, visual water covers the gap and the real basin is 1.5 m below it.
    _, data, scanner = scene('''<geom type="box" pos="-1.7 0 -.1" size="2.3 3 .1"/>
      <geom type="box" pos="2.5 0 -.01" size="1.9 3 .01" contype="0" conaffinity="0"/>
      <geom type="box" pos="2.5 0 -1.6" size="1.9 3 .1"/>''')
    result = scanner.measure(data, QUERY)
    assert result["valid"] and result["reason"] == "drop", result
    assert result["support_drop_m"] == pytest.approx(1.5)
    assert 0 < result["travel_clearance_m"] <= .6-.28


def test_missing_support_and_rise_fail_closed():
    _, data, scanner = scene('')
    assert scanner.measure(data, QUERY)["reason"] == "drop"
    _, data, scanner = scene(PLANE + '<geom type="box" pos=".7 0 .06" size=".15 1 .06"/>')
    result = scanner.measure(data, QUERY)
    assert result["reason"] in ("obstacle", "rise")
    assert result["travel_clearance_m"] < .4


def test_turn_checks_side_back_and_support():
    _, data, scanner = scene(PLANE + '<geom type="box" pos="-.25 .15 .6" size=".04 .04 .1"/>')
    assert scanner.measure(data, QUERY)["reason"] == "clear"
    result = scanner.measure(data, dict(QUERY, motion="turn"))
    assert not result["turn_clear"] and result["reason"] == "obstacle"
    _, data, scanner = scene('<geom type="box" pos="2 0 -.1" size="2.1 2 .1"/>')
    assert not scanner.measure(data, dict(QUERY, motion="turn"))["turn_clear"]


@pytest.mark.parametrize("change", [{"horizon_m": float("nan")}, {"sample_spacing_m": 0},
    {"sample_spacing_m": 1e-10}, {"support_body_names": ["missing"]}, {"motion": "flight"}])
def test_invalid_queries_cannot_report_clear(change):
    _, data, scanner = scene()
    assert not scanner.measure(data, dict(QUERY, **change))["valid"]


def test_world_operator_lease_inhibits_bus_motion_without_scanning():
    sim = WorldSim.__new__(WorldSim)
    sim._lock = threading.RLock()
    sim.scene_operator = SimpleNamespace(active=lambda: True)
    sim.spawned = True
    response = make_bus_handler(sim)({"op": "clearance", "query": QUERY})
    assert response == {"valid": False, "reason": "scene_test_active"}


def test_each_physics_step_observes_task_contacts(monkeypatch):
    # Drive the production loop for one multi-step tick; no thread/renderer is started.
    sim = WorldSim.__new__(WorldSim)
    sim.model, sim.data, _ = scene()
    sim._lock, sim._running = threading.RLock(), True
    sim.phys = {"steps_per_tick": 3, "realtime_factor": 1}
    order = []
    sim._apply_firmware = lambda: order.append("firmware")
    sim._update_imu = lambda _: setattr(sim, "_running", False)
    sim.scene_operator = SimpleNamespace(tick=lambda: order.append("tick"),
        runtime=SimpleNamespace(observe_step=lambda: order.append("observe")))
    actual_step = mujoco.mj_step
    def step(model, data):
        actual_step(model, data)
        order.append("step")
    monkeypatch.setattr(mujoco, "mj_step", step)
    sim._loop()
    assert order == ["firmware", "tick", "step", "observe"] * 3
