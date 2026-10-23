"""Physical scene qualification, independent of model or trained-policy performance."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

mujoco = pytest.importorskip("mujoco")
ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "worlds"
# Load the asset package through its location; the NERV host does not import scene facts.
spec = importlib.util.spec_from_file_location(
    "nerv_workbench_assets",
    WORLD / "scenes/so101_workbench/__init__.py",
    submodule_search_locations=[str(WORLD / "scenes/so101_workbench")],
)
assets = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = assets
spec.loader.exec_module(assets)
from nerv_workbench_assets.generate import build, CATALOG  # noqa: E402
from nerv_workbench_assets.evaluate import evaluate, hanoi_plan, object_points  # noqa: E402


def load(preset, tmp_path, **kwargs):
    path = tmp_path / f"{preset}.xml"
    meta = build(preset, path, **kwargs)
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data, meta


def advance(model, data, seconds):
    for _ in range(round(seconds / model.opt.timestep)):
        mujoco.mj_step(model, data)
    assert all(w.number == 0 for w in data.warning), [(w.number, w.lastinfo) for w in data.warning]
    assert np.isfinite(data.qpos).all()
    assert data.time == pytest.approx(seconds, abs=model.opt.timestep)


@pytest.mark.parametrize("preset", ["pick-place", "sort-snacks", "hanoi-five"])
def test_presets_have_valid_physics_and_no_initial_success(preset, tmp_path):
    model, data, meta = load(preset, tmp_path)
    assert model.nu == 6
    assert {"top", "wrist", "overview"} <= {model.camera(i).name for i in range(model.ncam)}
    advance(model, data, 2.0)
    assert not evaluate(model, data, meta)["success"]
    if preset == "hanoi-five":
        assert evaluate(model, data, meta)["stacks_bottom_to_top"] == [[0, 1, 2, 3, 4], [], []]


@pytest.mark.parametrize("item,flexes", [("snack_rigid", 0), ("cloth", 1), ("drink_can", 0)])
def test_object_replacement_changes_physical_model(item, flexes, tmp_path):
    model, data, meta = load("pick-place", tmp_path, replacements={"snack": item})
    assert model.nflex == flexes
    assert meta["objects"][0]["type"] == item
    advance(model, data, 1.0)


def test_soft_object_deforms_with_real_contacts(tmp_path):
    model, data, meta = load("pick-place", tmp_path)
    obj = meta["objects"][0]
    before = object_points(model, data, obj)
    before_dist = np.linalg.norm(before[:, None, :] - before[None, :, :], axis=-1)
    advance(model, data, 1.0)
    after = object_points(model, data, obj)
    # Pair distances survive rigid translation/rotation. At least 1% strain demonstrates
    # material deformation rather than floating point differences in rigid motion.
    after_dist = np.linalg.norm(after[:, None, :] - after[None, :, :], axis=-1)
    pairs = before_dist > 0
    assert np.max(np.abs(after_dist - before_dist)[pairs] / before_dist[pairs]) > 0.01
    assert any(int(c.flex[0]) >= 0 or int(c.flex[1]) >= 0 for c in data.contact)
    assert model.nflexvert > 8


def test_basket_has_open_top_and_real_containment(tmp_path):
    model, data, meta = load("pick-place", tmp_path, replacements={"snack": "snack_rigid"})
    obj = meta["objects"][0]
    basket = meta["baskets"][0]
    jid = model.joint("snack_free").id
    adr = model.jnt_qposadr[jid]
    # Controlled test initial condition: drop into the open basket. No policy is involved.
    data.qpos[adr : adr + 3] = [*basket["xy"], basket["rim_z"] + obj["size_m"][2]]
    advance(model, data, 1.5)
    assert evaluate(model, data, meta)["success"]
    # A released object outside the target must not pass.
    data.qpos[adr] = basket["xy"][0] + basket["inner_size_xy"][0]
    mujoco.mj_forward(model, data)
    assert not evaluate(model, data, meta)["success"]


def test_wrong_basket_and_partial_containment_fail(tmp_path):
    model, data, meta = load("sort-snacks", tmp_path)
    wrong = meta["baskets"][0]
    adr = model.jnt_qposadr[model.joint("cookies_free").id]
    data.qpos[adr : adr + 3] = [*wrong["xy"], wrong["bottom_z"] + 0.02]
    advance(model, data, 1.0)
    score = evaluate(model, data, meta)
    assert not next(o for o in score["objects"] if o["id"] == "cookies")["success"]


def test_motionless_object_inside_basket_without_support_fails(tmp_path):
    model, data, meta = load("pick-place", tmp_path, replacements={"snack": "snack_rigid"})
    basket = meta["baskets"][0]
    adr = model.jnt_qposadr[model.joint("snack_free").id]
    data.qpos[adr : adr + 3] = [*basket["xy"], (basket["rim_z"] + basket["bottom_z"]) / 2]
    mujoco.mj_forward(model, data)
    row = evaluate(model, data, meta)["objects"][0]
    assert row["inside"] and row["resting"] and row["released"]
    assert not row["supported"] and not row["success"]


def test_partially_contained_or_spinning_object_is_not_completed(tmp_path):
    model, data, meta = load("pick-place", tmp_path, replacements={"snack": "snack_rigid"})
    obj = meta["objects"][0]
    basket = meta["baskets"][0]
    joint = model.joint("snack_free").id
    adr = model.jnt_qposadr[joint]
    dadr = model.jnt_dofadr[joint]
    data.qpos[adr : adr + 3] = [*basket["xy"], basket["rim_z"] + obj["size_m"][2]]
    advance(model, data, 1.5)
    data.qvel[dadr + 3 : dadr + 6] = [0, 0, 10]
    mujoco.mj_forward(model, data)
    row = evaluate(model, data, meta)["objects"][0]
    assert row["inside"] and not row["resting"] and not row["success"]
    data.qvel[:] = 0
    data.qpos[adr] = basket["xy"][0] + basket["inner_size_xy"][0] / 2 - obj["size_m"][0] / 4
    mujoco.mj_forward(model, data)
    row = evaluate(model, data, meta)["objects"][0]
    assert not row["inside"] and not row["success"]


def test_disk_collision_has_actual_hole(tmp_path):
    model, data, meta = load("hanoi-five", tmp_path)
    disk = model.body("disk_0").id
    mask = np.zeros(6, dtype=np.uint8)
    mask[5] = 1
    model.geom_group[:] = 0
    model.geom_group[model.geom_bodyid == disk] = 5
    center = data.xpos[disk].copy()
    center[2] += 0.02
    gid = np.zeros(1, dtype=np.int32)
    through = mujoco.mj_ray(model, data, center, np.array([0.0, 0.0, -1.0]), mask, 1, -1, gid)
    assert through < 0
    radius = (meta["hanoi"]["disk_radii_m"][0] + meta["hanoi"]["hole_radius_m"]) / 2
    for sector in range(meta["hanoi"]["segments"]):
        # Probe each sector's interior; an exact seam is numerically ambiguous to ray casting.
        angle = 2 * np.pi * (sector + 0.5) / meta["hanoi"]["segments"]
        point = center + np.array([radius * np.cos(angle), radius * np.sin(angle), 0])
        assert mujoco.mj_ray(model, data, point, np.array([0.0, 0.0, -1.0]), mask, 1, -1, gid) > 0


def test_hanoi_solver_is_legal_and_minimal():
    moves = hanoi_plan(5)
    assert len(moves) == 31
    stacks = [list(range(5)), [], []]
    for move in moves:
        disk = stacks[move["from"]].pop()
        assert disk == move["disk"]
        target = stacks[move["to"]]
        assert not target or disk > target[-1]
        target.append(disk)
    assert stacks == [[], [], list(range(5))]


def test_hanoi_physical_target_stack_scores_after_settling(tmp_path):
    model, data, meta = load("hanoi-five", tmp_path)
    target = meta["hanoi"]["peg_xy"][meta["hanoi"]["target_peg"]]
    # A known test initial condition exercises scoring; it is not a robot demonstration.
    for disk in meta["hanoi"]["disks"]:
        joint = model.joint(f"{disk['body']}_free").id
        adr = model.jnt_qposadr[joint]
        data.qpos[adr : adr + 2] = target
        data.qpos[adr + 2] += meta["validation"]["surface_tolerance_m"] / 2
    mujoco.mj_forward(model, data)
    assert not evaluate(model, data, meta)["success"]  # The stack has no base support yet.
    advance(model, data, 2.0)
    assert evaluate(model, data, meta)["success"]


def test_configuration_rejects_unknown_slots_and_nonphysical_mass(tmp_path):
    with pytest.raises(ValueError, match="unknown object slots"):
        load("pick-place", tmp_path, replacements={"missing": "cloth"})
    with pytest.raises(ValueError, match="unknown object"):
        load("pick-place", tmp_path, replacements={"snack": "not_registered"})
    catalog = json.loads(CATALOG.read_text())
    catalog["soft_snack"]["mass_kg"] = -1
    path = tmp_path / "objects.json"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="positive"):
        load("pick-place", tmp_path, catalog=path)


def test_registry_exposes_workbench_without_changing_real_binding():
    from nerv.platform.registry import Registry
    from nerv.platform.registry.compat import check

    registry = Registry()
    body = registry.body("arm-lerobot-so101")
    assert body.buses["sim"].enabled
    assert body.buses["real"].provider == "lerobot0.6.1"
    assert not body.buses["real"].verified
    for preset in ("pick-place", "sort-snacks", "hanoi-five"):
        assert check(registry.world(f"so101-{preset}"), body) == (True, "")


@pytest.mark.parametrize("preset", ["pick-place", "sort-snacks", "hanoi-five"])
def test_world_motor_dispatch_and_reset_use_named_start_pose(preset, tmp_path):
    from nerv.world.mujoco_node import WorldSim, make_bus_handler

    model, data, meta = load(preset, tmp_path)
    sim = WorldSim(
        str(tmp_path / f"{preset}.xml"),
        physics={"dt": model.opt.timestep},
        spawn={"initial_joints": meta["initial_joints"]},
    )
    try:
        handle = make_bus_handler(sim)
        initial = sim.data.qpos.copy()
        joints = list(meta["initial_joints"])
        info = handle({"op": "spawn", "joint_names": joints, "pd_mode": "position_actuator"})
        assert not info["has_free_base"]
        state = handle({"op": "read"})
        assert state["joint_pos"] == pytest.approx(list(meta["initial_joints"].values()))
        assert not state["extra"]  # Scene task truth is not inserted into the body observation.
        targets = list(state["joint_pos"])
        targets[0] += 0.15
        handle({"op": "write", "targets": targets})
        for _ in range(600):
            sim._apply_firmware()
            mujoco.mj_step(sim.model, sim.data)
        assert abs(handle({"op": "read"})["joint_pos"][0] - targets[0]) < 0.03
        assert handle({"op": "read"})["t"] > state["t"]
        handle({"op": "reset"})
        assert sim.data.qpos == pytest.approx(initial)
        assert handle({"op": "read"})["joint_pos"] == pytest.approx(state["joint_pos"])
        assert {"camera:top", "camera:wrist"} <= set(handle({"op": "sensors"})["sensors"])
    finally:
        sim.stop()


@pytest.mark.skipif(
    os.environ.get("MUJOCO_GL") == "disable", reason="rendering disabled explicitly"
)
def test_workbench_camera_http_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from nerv.world.mujoco_node import WorldSim, build_app
    import io
    from PIL import Image

    model, data, meta = load("sort-snacks", tmp_path)
    sim = WorldSim(
        str(tmp_path / "sort-snacks.xml"),
        physics={"dt": model.opt.timestep},
        spawn={"initial_joints": meta["initial_joints"]},
    )
    try:
        with TestClient(build_app(sim, [])) as client:
            for camera in ("top", "wrist", "overview"):
                response = client.get(f"/sensors/camera:{camera}")
                assert response.status_code == 200
                frame = np.asarray(Image.open(io.BytesIO(response.content)))
                assert frame.shape == (480, 640, 3)
                assert frame.std() > 20
    finally:
        sim.stop()


@pytest.mark.parametrize("pose", [{"missing": 0}, {"shoulder_pan": 99}, {"gripper": float("nan")}])
def test_world_rejects_invalid_start_pose(pose, tmp_path):
    from nerv.world.mujoco_node import WorldSim

    load("pick-place", tmp_path)
    with pytest.raises(ValueError, match="spawn.initial_joints"):
        WorldSim(str(tmp_path / "pick-place.xml"), spawn={"initial_joints": pose})
