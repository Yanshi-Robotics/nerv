"""Verify a narrow doorway and turn-space rejection using the real CPU G1 policy.

WorldSim owns the only physics clock; no renderer, ports, pose pinning or
replacement gait is used. Each independent initial pose is set by normal reset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]

# Acceptance parameters only; production collision and gait settings are unchanged.
APPROACH_M = 1.0  # start one metre from each side of the source-authored door plane
WALL_ROOT_GAP_M = 0.27  # requested root margin in addition to the source wall thickness
SETTLE_S = 3.0  # check released-policy standing before and after each action
SAMPLE_PERIOD_S = 0.05  # retained diagnostic samples; maxima still use every physics step
CONTACT_TOLERANCE_M = 0.005  # same physical-obstacle tolerance as the existing hazard harness
TURN_REQUEST_DEG = 90.0  # deliberately impossible turn when the swept envelope meets a wall
TURN_MAX_DRIFT_DEG = 5.0  # a rejected turn may sway slightly, but must not execute the request
POLL_S = 0.02  # yields to the real physics and policy threads


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Expected a finite positive number")
    return number


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def execute(args):
    import numpy as np

    from nerv.platform.registry import Registry
    from nerv.world.mujoco_node import SceneLayout, WorldSim, _body_defaults_from_registry
    from nerv.body.families.humanoid import HumanoidBody
    from nerv.body.skills import SkillRunner, StopFlag
    from scenes.explore import source_files, source_hash
    from tools.benchmark_residences import LocalBus

    root, output = args.repository_root, args.output
    started = time.monotonic()
    deadline = started + args.limit_seconds
    expired = threading.Event()
    stop = StopFlag()

    def watchdog():
        expired.set()
        stop.request()

    timer = threading.Timer(args.limit_seconds, watchdog)
    timer.daemon = True
    timer.start()
    report = dict(
        created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        mode="CPU WorldSim + original G1 released policy + LocalBus",
        physics_clock="WorldSim only; observer does not call mj_step",
        initial_pose="Normal reset before each case; no pose writes or pinning during actions",
        limit_seconds=args.limit_seconds,
        source_sha256={
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__).resolve(),
                root / "src/nerv/world/mujoco_node.py",
                root / "src/nerv/world/clearance.py",
                root / "src/nerv/body/families/humanoid.py",
                root / "bodies/humanoid-unitree-g1/body.yaml",
                root / "policies/g1-29dof-turn/policy.onnx",
                root / "policies/g1-29dof-turn/contract.json",
                root / "worlds/build/apt-g1.xml",
            )
        },
        world_source_hash=source_hash(source_files("apt")),
        cases=[],
    )
    reg = Registry()
    world, spec = reg.world("apt"), reg.body("humanoid-unitree-g1")
    sim = WorldSim(
        str(Path(world.assets_root) / world.supports[spec.name]),
        physics=world.physics,
        layout=SceneLayout(world.assets_root, "apt"),
        spawn=world.spawn,
        body_defaults=_body_defaults_from_registry(spec),
    )
    body = None
    original_observer = None
    monitor = None

    def check_budget():
        if expired.is_set() or time.monotonic() >= deadline:
            stop.request()
            raise TimeoutError("Confined acceptance exhausted its wall-time budget")

    def settle(seconds):
        target = float(sim.data.time) + seconds
        while True:
            check_budget()
            with sim._lock:
                if sim.data.time >= target:
                    return
                if not sim.physics_alive():
                    raise RuntimeError("The existing WorldSim physics loop stopped advancing")
            time.sleep(POLL_S)

    def snapshot():
        state = sim.status()
        state["exact_xy"] = sim.data.xpos[sim.base_body, :2].tolist()
        state["yaw_rad"] = float(sim._base_pose()[2])
        state["speed_m_s"] = float(np.linalg.norm(sim.data.qvel[sim.free_dadr : sim.free_dadr + 3]))
        state["policy_error"] = body._policy_error
        return state

    def contacts():
        all_depth, obstacle_depth, pairs = 0.0, 0.0, []
        for con in sim.data.contact:
            a, b = int(con.geom1), int(con.geom2)
            a_robot = sim.model.body_rootid[sim.model.geom_bodyid[a]] == sim.base_body
            b_robot = sim.model.body_rootid[sim.model.geom_bodyid[b]] == sim.base_body
            if a_robot == b_robot:
                continue
            other = b if a_robot else a
            name = sim.model.geom(other).name
            floor = other in floor_ids
            depth = max(0.0, -float(con.dist))
            all_depth = max(all_depth, depth)
            if not floor:
                obstacle_depth = max(obstacle_depth, depth)
                if con.dist <= 0:
                    pairs.append(
                        dict(
                            robot=sim.model.geom(a if a_robot else b).name,
                            obstacle=name,
                            penetration_m=depth,
                        )
                    )
        return all_depth, obstacle_depth, pairs

    def robot_bounds():
        ids = np.array(
            [
                gid
                for gid in range(sim.model.ngeom)
                if sim.model.body_rootid[sim.model.geom_bodyid[gid]] == sim.base_body
                and (sim.model.geom_contype[gid] or sim.model.geom_conaffinity[gid])
            ]
        )
        rotation = sim.data.geom_xmat[ids].reshape(-1, 3, 3)
        center = sim.data.geom_xpos[ids] + np.einsum(
            "nij,nj->ni", rotation, sim.model.geom_aabb[ids, :3]
        )
        half = np.einsum("nij,nj->ni", np.abs(rotation), sim.model.geom_aabb[ids, 3:])
        return np.array([(center - half).min(axis=0), (center + half).max(axis=0)])

    class Monitor:
        def __init__(self):
            self.steps = 0
            self.samples = []
            self.error = None
            self.max_all = self.max_obstacle = self.max_tilt = self.max_speed = 0.0
            self.fell = False
            self.policy_errors = set()
            self.pairs = {}
            self.stop_time = None
            self.post_stop_max_speed = 0.0
            self.next_sample = float(sim.data.time)

        def observe(self):
            try:
                self.steps += 1
                state = snapshot()
                all_depth, obstacle_depth, pairs = contacts()
                self.max_all = max(self.max_all, all_depth)
                self.max_obstacle = max(self.max_obstacle, obstacle_depth)
                self.max_tilt = max(self.max_tilt, state["tilt_deg"])
                self.max_speed = max(self.max_speed, state["speed_m_s"])
                self.fell |= bool(state["fallen"])
                if state["policy_error"]:
                    self.policy_errors.add(state["policy_error"])
                for pair in pairs:
                    key = pair["robot"] + "/" + pair["obstacle"]
                    self.pairs[key] = max(self.pairs.get(key, 0.0), pair["penetration_m"])
                if self.stop_time is not None and sim.data.time >= self.stop_time + SETTLE_S / 2:
                    self.post_stop_max_speed = max(self.post_stop_max_speed, state["speed_m_s"])
                if sim.data.time >= self.next_sample:
                    self.samples.append(
                        dict(
                            **state,
                            all_contact_penetration_m=all_depth,
                            obstacle_penetration_m=obstacle_depth,
                        )
                    )
                    self.next_sample = float(sim.data.time) + SAMPLE_PERIOD_S
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"
                stop.request()

        def summary(self):
            return dict(
                physics_steps=self.steps,
                max_all_contact_penetration_m=self.max_all,
                max_obstacle_penetration_m=self.max_obstacle,
                max_tilt_deg=self.max_tilt,
                max_speed_m_s=self.max_speed,
                post_stop_max_speed_m_s=self.post_stop_max_speed,
                fell=self.fell,
                policy_errors=sorted(self.policy_errors),
                physical_obstacle_contacts=self.pairs,
                observer_error=self.error,
            )

    try:
        body = HumanoidBody(spec.model_dump(), LocalBus(sim), SkillRunner(stop))
        layout = sim.layout.layout
        door = min((d for d in layout.DOORS if d["floor"] == 0), key=lambda d: d["width"])
        if door["orient"] != "v":
            raise ValueError("This authored two-sided case expects the minimum-width vertical door")
        foyer = layout.ROOMS["foyer"]["rect"]
        floor_ids = {
            g
            for g in range(sim.model.ngeom)
            if any(sim.model.geom(g).name == room + "_floor" for room in layout.ROOMS)
        }
        report["floor_support_geoms"] = [sim.model.geom(g).name for g in sorted(floor_ids)]
        jambs = []
        for gid in range(sim.model.ngeom):
            name = sim.model.geom(gid).name
            pos = sim.data.geom_xpos[gid]
            if (
                "_df" not in name
                or name.endswith("_top")
                or abs(pos[0] - door["coord"]) > layout.WALL_THICK + layout.DOOR_FRAME_THICK
                or abs(pos[1] - door["center"]) > door["width"]
            ):
                continue
            rotation = sim.data.geom_xmat[gid].reshape(3, 3)
            center = pos + rotation @ sim.model.geom_aabb[gid, :3]
            half = np.abs(rotation) @ sim.model.geom_aabb[gid, 3:]
            jambs.append(
                dict(geom=name, low=(center - half).tolist(), high=(center + half).tolist())
            )
        lower = [j["high"][1] for j in jambs if j["high"][1] < door["center"]]
        upper = [j["low"][1] for j in jambs if j["low"][1] > door["center"]]
        if not lower or not upper:
            raise ValueError("Compiled physical door jambs were not found")
        report["door_geometry"] = dict(
            source=door,
            physical_jambs=jambs,
            clear_y=[max(lower), min(upper)],
            clear_width_m=min(upper) - max(lower),
        )
        original_wall_xy = [
            (foyer[0] + foyer[2]) / 2,
            foyer[1] + layout.WALL_THICK + WALL_ROOT_GAP_M,
        ]
        wall_xy = list(original_wall_xy)
        wall_position = dict(mode=args.wall_pose, original_requested_xy=original_wall_xy)
        if args.wall_pose == "geometry-clear":
            # Measure reset geometry and reuse the configured allowance for gait sway.
            with sim._lock:
                sim.spawn_pose = (*original_wall_xy, spec.actuators["start_height"], 0.0)
                sim.reset()
                south_extent = original_wall_xy[1] - robot_bounds()[0, 1]
            door_left = layout.FRONT_DOOR["center"] - layout.FRONT_DOOR["width"] / 2
            wall_xy = [
                (foyer[0] + layout.WALL_THICK + door_left) / 2,
                foyer[1]
                + layout.WALL_THICK
                + south_extent
                + body._clearance_query["envelope_margin_m"],
            ]
            wall_position.update(
                measured_south_extent_m=float(south_extent),
                configured_envelope_margin_m=body._clearance_query["envelope_margin_m"],
                source_wall_segment_x=[foyer[0] + layout.WALL_THICK, door_left],
            )
        cases = [
            dict(
                id="narrow_door_eastbound",
                kind="door",
                sign=1,
                xy=[door["coord"] - APPROACH_M, door["center"]],
                yaw=0.0,
                source_door=door,
                tool="move_forward",
                args={"meters": 2 * APPROACH_M},
            ),
            dict(
                id="narrow_door_westbound",
                kind="door",
                sign=-1,
                xy=[door["coord"] + APPROACH_M, door["center"]],
                yaw=math.pi,
                source_door=door,
                tool="move_forward",
                args={"meters": 2 * APPROACH_M},
            ),
            dict(
                id="foyer_south_wall_turn",
                kind="turn",
                xy=wall_xy,
                yaw=0.0,
                source_room="foyer",
                source_rect=list(foyer),
                position_derivation=wall_position,
                tool="turn_left",
                args={"degrees": TURN_REQUEST_DEG},
            ),
        ]
        if args.case:
            cases = [case for case in cases if case["id"] == args.case]
        report["requested_cases"] = [case["id"] for case in cases]
        original_observer = sim.scene_operator.runtime.observe_step

        def observe_step():
            original_observer()
            if monitor is not None:
                monitor.observe()

        sim.scene_operator.runtime.observe_step = observe_step
        sim.start()
        for case in cases:
            check_budget()
            record = dict(**case, passed=False)
            report["cases"].append(record)
            try:
                with sim._lock:
                    monitor = None
                    sim.spawn_pose = (
                        *case["xy"],
                        layout.FLOOR_Z(0) + spec.actuators["start_height"],
                        case["yaw"],
                    )
                    sim.reset()
                    record["initial_reset"] = snapshot()
                    record["initial_contacts"] = contacts()[2]
                    record["initial_clearance"] = {
                        kind: sim.clearance(dict(body._clearance_query, motion=kind))
                        for kind in ("forward", "turn")
                    }
                if record["initial_contacts"]:
                    raise RuntimeError("Initial source-derived pose intersects non-floor geometry")
                with sim._lock:
                    monitor = Monitor()
                body.release()
                settle(SETTLE_S)
                with sim._lock:
                    record["initial_settle_metrics"] = monitor.summary()
                    dump(output / (case["id"] + "-settle-samples.json"), monitor.samples)
                    record["before"] = snapshot()
                    record["robot_bounds_before"] = robot_bounds().tolist()
                    record["contacts_before_action"] = contacts()[2]
                    record["clearance_before_action"] = {
                        kind: sim.clearance(dict(body._clearance_query, motion=kind))
                        for kind in ("forward", "turn")
                    }
                    if record["before"]["fallen"] or record["before"]["policy_error"]:
                        raise RuntimeError("The initial released-policy pose did not stabilize")
                    if record["contacts_before_action"]:
                        raise RuntimeError("The standing robot already contacts non-floor geometry")
                    if (
                        monitor.fell
                        or monitor.error
                        or monitor.policy_errors
                        or monitor.max_obstacle > CONTACT_TOLERANCE_M
                    ):
                        raise RuntimeError("The initial standing interval was physically invalid")
                    monitor = Monitor()
                check_budget()
                record["action"] = body.invoke(case["tool"], **case["args"])
                with sim._lock:
                    monitor.stop_time = float(sim.data.time)
                settle(SETTLE_S)
                with sim._lock:
                    record["after"] = snapshot()
                    record["robot_bounds_after"] = robot_bounds().tolist()
                    record["metrics"] = monitor.summary()
                    retained_samples = monitor.samples
                    monitor = None
                before, after = record["before"], record["after"]
                delta = np.array(after["exact_xy"]) - np.array(before["exact_xy"])
                record["measured_displacement_m"] = float(np.linalg.norm(delta))
                yaw = after["yaw_rad"] - before["yaw_rad"]
                record["measured_turn_deg"] = math.degrees(math.atan2(math.sin(yaw), math.cos(yaw)))
                metrics, action = record["metrics"], record["action"].get("data", {})
                common = (
                    not metrics["fell"]
                    and not metrics["policy_errors"]
                    and metrics["observer_error"] is None
                    and metrics["max_obstacle_penetration_m"] <= CONTACT_TOLERANCE_M
                    and metrics["post_stop_max_speed_m_s"] <= sim.phys["scene_still_speed_m_s"]
                )
                if case["kind"] == "door":
                    record["beyond_wall_m"] = case["sign"] * (after["exact_xy"][0] - door["coord"])
                    if case["sign"] > 0:
                        record["full_body_beyond_jamb_m"] = record["robot_bounds_after"][0][
                            0
                        ] - max(j["high"][0] for j in jambs)
                    else:
                        record["full_body_beyond_jamb_m"] = (
                            min(j["low"][0] for j in jambs) - record["robot_bounds_after"][1][0]
                        )
                    record["passed"] = bool(
                        common
                        and record["full_body_beyond_jamb_m"] > 0
                        and action.get("reason") == "reached"
                    )
                else:
                    record["passed"] = bool(
                        common
                        and action.get("reason") == "braked"
                        and record["action"].get("ok") is False
                        and action.get("braked") is True
                        and action.get("motion_clearance", {}).get("valid") is True
                        and action.get("motion_clearance", {}).get("turn_clear") is False
                        and action.get("motion_clearance", {}).get("reason") == "obstacle"
                        and abs(record["measured_turn_deg"]) <= TURN_MAX_DRIFT_DEG
                    )
                dump(output / (case["id"] + "-samples.json"), retained_samples)
            except Exception as error:
                with sim._lock:
                    if monitor is not None:
                        record["metrics"] = monitor.summary()
                        dump(output / (case["id"] + "-samples.json"), monitor.samples)
                    monitor = None
                record["error"] = f"{type(error).__name__}: {error}"
            dump(output / (case["id"] + ".json"), record)
            dump(output / "report.json", report)
            print(
                json.dumps(
                    {
                        key: record[key]
                        for key in (
                            "id",
                            "passed",
                            "error",
                            "action",
                            "metrics",
                            "measured_displacement_m",
                            "measured_turn_deg",
                            "beyond_wall_m",
                        )
                        if key in record
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if expired.is_set():
                break
        report["renderer_initialized"] = sim.render._renderer is not None
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        timer.cancel()
        try:
            if body is not None:
                body.close()
        finally:
            sim.stop()
            if original_observer is not None:
                sim.scene_operator.runtime.observe_step = original_observer
        report["elapsed_wall_s"] = time.monotonic() - started
        report["budget_expired"] = expired.is_set()
        report["passed"] = (
            len(report["cases"]) == len(report.get("requested_cases", []))
            and bool(report["cases"])
            and all(c["passed"] for c in report["cases"])
            and not report.get("error")
            and not report["budget_expired"]
        )
        dump(output / "report.json", report)
    return bool(report["passed"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit-seconds", type=positive_float, default=300.0)
    parser.add_argument(
        "--case",
        choices=("narrow_door_eastbound", "narrow_door_westbound", "foyer_south_wall_turn"),
    )
    parser.add_argument(
        "--wall-pose",
        choices=("geometry-clear", "original"),
        default="geometry-clear",
        help="Measured body-envelope clearance, or the rejected original initial point",
    )
    args = parser.parse_args()
    args.repository_root = ROOT
    args.output = args.output.resolve()
    if args.output.exists():
        parser.error("Use a new output directory to preserve earlier failures and evidence")
    args.output.mkdir(parents=True)
    os.environ["MUJOCO_GL"] = "disable"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["NERV_HOME"] = str(args.output / "nerv-home")
    sys.path[:0] = [str(args.repository_root / "src"), str(args.repository_root / "worlds")]
    return 0 if execute(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
