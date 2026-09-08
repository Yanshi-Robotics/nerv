"""Run CPU G1 approach/braking tests against physical residence hazards.

WorldSim owns the only physics clock. Reset establishes each initial pose;
walking uses the released policy, and chair preparation uses real grab forces.
No renderer, port, model provider, pose pinning or replacement gait is used.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def execute(args):
    import mujoco
    import numpy as np

    from nerv.platform.registry import Registry
    from nerv.world.mujoco_node import WorldSim, SceneLayout, _body_defaults_from_registry
    from nerv.world.clearance import RAY_MASK
    from nerv.body.families.humanoid import HumanoidBody
    from nerv.body.skills import SkillRunner, StopFlag
    from tools.benchmark_residences import LocalBus
    from scenes.paths import residence_routes

    # Acceptance settings, separate from product control parameters.
    START_GAP_M = 1.30  # leave room for real approach before the 0.35 m braking margin
    SPAWN_REAR_MARGIN_M = 0.40  # root clearance from the narrow stair landing's rear wall
    START_SETTLE_S = 3.0
    AFTER_SETTLE_S = 3.0
    SAMPLE_PERIOD_S = 0.02  # measurement cadence only; physics remains 500 Hz
    WALK_REQUEST_M = 3.0  # deliberately requests travel beyond every chosen hazard
    MIN_APPROACH_M = 0.10  # distinguish an actual braking run from a zero-motion preflight
    CONTACT_TOLERANCE_M = 0.005  # soft-contact numerical tolerance, not permitted deep penetration
    CHAIR_LIFT_M = 0.12  # lift clear of floor friction using the existing grab force
    CHAIR_LIFT_S = 1.5
    CHAIR_TRANSLATE_S = 2.5
    CHAIR_SETTLE_S = 2.0
    HEIGHT_PROBE_COUNT = 36  # diagnostic rays only; the production scanner owns navigation

    def dump(path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))

    def collision_ids(sim, fragment):
        return np.array(
            [
                g
                for g in range(sim.model.ngeom)
                if fragment in sim.model.geom(g).name
                and (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g])
            ],
            dtype=int,
        )

    def bounds(sim, ids):
        if not len(ids):
            raise ValueError("No physical geometry matched the authored obstacle")
        m, d = sim.model, sim.data
        rot = d.geom_xmat[ids].reshape(-1, 3, 3)
        center = d.geom_xpos[ids] + np.einsum("nij,nj->ni", rot, m.geom_aabb[ids, :3])
        half = np.einsum("nij,nj->ni", np.abs(rot), m.geom_aabb[ids, 3:])
        return np.array([(center - half).min(axis=0), (center + half).max(axis=0)])

    def projected_edge(box, direction, *, far=False):
        # Projection of the axis-aligned bounding box onto the travel direction.
        low, high = box[:, :2]
        return float(
            np.sum(np.where(direction >= 0, high if far else low, low if far else high) * direction)
        )

    def feet_ids(sim, spec):
        bids = {sim.model.body(name).id for name in spec.actuators["foot_bodies"]}
        ids = []
        for gid in range(sim.model.ngeom):
            if not (sim.model.geom_contype[gid] or sim.model.geom_conaffinity[gid]):
                continue
            bid = int(sim.model.geom_bodyid[gid])
            while bid and bid not in bids:
                bid = int(sim.model.body_parentid[bid])
            if bid in bids:
                ids.append(gid)
        return np.array(ids, dtype=int)

    def authored_cases(sim, scene):
        L = sim.layout.layout
        if scene == "house":
            pool = L.ESTATE["pool"]
            coping = bounds(sim, collision_ids(sim, "h2_pool_coping0"))
            gate = bounds(sim, collision_ids(sim, "h2_estate_gate"))
            return [
                dict(
                    id="house_pool_edge",
                    xy=[coping[0, 0] - START_GAP_M, (pool[1] + pool[3]) / 2],
                    yaw=0.0,
                    direction=[1.0, 0.0],
                    boundary=pool[0],
                    obstacle="h2_pool",
                    kind="unloaded_visual_water_over_physical_basin",
                ),
                dict(
                    id="house_closed_gate",
                    xy=[L.ESTATE["gate_center"][0], gate[1, 1] + START_GAP_M],
                    yaw=-math.pi / 2,
                    direction=[0.0, -1.0],
                    obstacle="h2_estate_gate",
                    kind="high_barrier",
                ),
            ]
        stair = next(item for item in L.STAIRS if item["name"] == "stair0_up")
        landing = L.FLOOR_LANDING_RECT
        stair_gap = min(
            START_GAP_M, stair["start_xy"][0] - landing[0] - L.WALL_THICK - SPAWN_REAR_MARGIN_M
        )
        table = bounds(sim, collision_ids(sim, "furn_dn_table"))
        coffee = bounds(sim, collision_ids(sim, "furn_gr_coffee"))
        dining_room = L.ROOMS["dining"]["rect"]
        table_gap = min(
            START_GAP_M, dining_room[3] - L.WALL_THICK - SPAWN_REAR_MARGIN_M - table[1, 1]
        )
        dining_route = next(r for r in residence_routes(L) if r["id"] == "living_dining")
        return [
            dict(
                id="apt_stair_entry",
                xy=[stair["start_xy"][0] - stair_gap, stair["start_xy"][1]],
                yaw=0.0,
                direction=[1.0, 0.0],
                boundary=stair["start_xy"][0],
                obstacle="stair0_up",
                kind="low_riser_exceeds_flat_policy_limit",
            ),
            dict(
                id="apt_stair_descent",
                xy=[
                    stair["start_xy"][0] - stair_gap,
                    next(s for s in L.STAIRS if s["name"] == "stair0_dn")["start_xy"][1],
                ],
                floor_z=L.FLOOR_Z(1),
                yaw=0.0,
                direction=[1.0, 0.0],
                boundary=stair["start_xy"][0],
                obstacle="stair0_dn",
                kind="unsupported_descent_beyond_flat_policy_limit",
            ),
            dict(
                id="apt_low_coffee_table",
                xy=[coffee[0, 0] - START_GAP_M, float(coffee[:, 1].mean())],
                yaw=0.0,
                direction=[1.0, 0.0],
                obstacle="furn_gr_coffee",
                kind="low_furniture",
            ),
            dict(
                id="apt_mid_tabletop",
                xy=[float(table[:, 0].mean()), table[1, 1] + table_gap],
                yaw=-math.pi / 2,
                direction=[0.0, -1.0],
                obstacle="furn_dn_table",
                kind="thin_mid_height_tabletop",
            ),
            dict(
                id="apt_physically_moved_chair",
                xy=dining_route["points"][-2][:2],
                yaw=math.pi,
                direction=[-1.0, 0.0],
                obstacle="furn_dn_e1",
                kind="force_moved_free_chair",
                chair_body="ix_dn_e1",
            ),
        ]

    def static_check(sim, body, case, foot_ids):
        with sim._lock:
            result = {
                "forward": sim.clearance(dict(body._clearance_query, motion="forward")),
                "turn": sim.clearance(dict(body._clearance_query, motion="turn")),
            }
            floor = bounds(sim, foot_ids)[0, 2]
            top = sim.data.xpos[sim.base_body, 2] + body.spec["actuators"]["start_height"]
            origin = sim.data.xpos[sim.base_body].copy()
            direction = np.array([*case["direction"], 0.0])
            hit = np.zeros(1, np.int32)
            seen = {}
            for height in np.linspace(
                min(body._clearance_query["height_offsets_m"]), top - floor, HEIGHT_PROBE_COUNT
            ):
                origin[2] = floor + height
                distance = mujoco.mj_ray(
                    sim._clearance_scanner._view, sim.data, origin, direction, RAY_MASK, 1, -1, hit
                )
                if distance >= 0:
                    name = sim.model.geom(int(hit[0])).name
                    seen.setdefault(name, {"distance_m": float(distance), "heights_world_m": []})[
                        "heights_world_m"
                    ].append(float(origin[2]))
            result["centreline_collision_height_probes"] = seen
            return result

    class Monitor:
        def __init__(self, sim, body, case, foot_ids):
            self.sim, self.body, self.case, self.feet = sim, body, case, foot_ids
            self.ids = set(collision_ids(sim, case["obstacle"]).tolist())
            self.samples = []
            self.error = None
            self.done = threading.Event()
            self.thread = threading.Thread(target=self.run, daemon=True)

        def take(self):
            s, c = self.sim, self.case
            with s._lock:
                state = s.status()
                direction = np.array(c["direction"])
                edge = c.get("boundary")
                if edge is None:
                    edge = projected_edge(bounds(s, np.array(sorted(self.ids))), direction)
                feet_edge = projected_edge(bounds(s, self.feet), direction, far=True)
                penetration, contacts = 0.0, []
                for con in s.data.contact:
                    a, b = int(con.geom1), int(con.geom2)
                    other = b if a in self.ids else a if b in self.ids else None
                    if (
                        other is not None
                        and s.model.body_rootid[s.model.geom_bodyid[other]] == s.base_body
                    ):
                        penetration = max(penetration, -float(con.dist))
                        contacts.append([s.model.geom(a).name, s.model.geom(b).name])
                self.samples.append(
                    dict(
                        t=float(s.data.time),
                        base=state["base"],
                        fallen=state["fallen"],
                        tilt_deg=state["tilt_deg"],
                        feet_before_boundary_m=float(edge - feet_edge),
                        hazard_penetration_m=penetration,
                        hazard_contacts=contacts,
                        base_speed_m_s=float(
                            np.linalg.norm(s.data.qvel[s.free_dadr : s.free_dadr + 3])
                        ),
                        policy_error=self.body._policy_error,
                    )
                )

        def run(self):
            try:
                while not self.done.is_set():
                    self.take()
                    self.done.wait(SAMPLE_PERIOD_S)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                self.done.set()

        def start(self):
            self.take()
            self.thread.start()

        def stop(self):
            self.done.set()
            self.thread.join(timeout=1)
            self.take()

    def move_chair(sim, case):
        """Use the real operator lease/Interaction forces, then release before walking."""
        with sim._lock:
            op = sim.scene_operator
            bid = sim.model.body(case["chair_body"]).id
            facilities = op.runtime.catalogue()["facilities"]
            entry = next(
                f
                for f in facilities
                if f.get("body") == case["chair_body"] or f["id"] == case["chair_body"]
            )
            before = sim.data.xpos[bid].copy()
            lease = op.acquire("hazard-acceptance", "cpu-operator", sim.epoch)
            credentials = dict(
                session="hazard-acceptance",
                owner="cpu-operator",
                token=lease["token"],
                epoch=sim.epoch,
            )
            point = sim.data.xipos[bid].copy()
            target = point.copy()
            target[0] = (before[0] + sim.layout.layout.ROOMS["dining"]["rect"][2]) / 2
            op.runtime.interaction.grab(bid, point)
            start = float(sim.data.time)
        while True:
            with sim._lock:
                elapsed = float(sim.data.time) - start
                op.check(**credentials, renew=True)
                wanted = point.copy()
                wanted[2] += CHAIR_LIFT_M * min(1.0, elapsed / CHAIR_LIFT_S)
                wanted[0] += (target[0] - point[0]) * min(
                    1.0, max(0.0, elapsed - CHAIR_LIFT_S) / CHAIR_TRANSLATE_S
                )
                op.runtime.interaction.move_grab(wanted)
                if sim.status()["fallen"]:
                    raise RuntimeError("Robot fell while standing during chair preparation")
                if elapsed >= CHAIR_LIFT_S + CHAIR_TRANSLATE_S:
                    op.runtime.interaction.release()
                    op.revoke("chair_preparation_complete")
                    break
            time.sleep(SAMPLE_PERIOD_S)
        time.sleep(CHAIR_SETTLE_S)
        with sim._lock:
            after = sim.data.xpos[bid].copy()
            return dict(
                method="SceneOperator lease and Interaction grab forces; released before G1 motion",
                catalogue_entry=entry,
                before=before.tolist(),
                after=after.tolist(),
                displacement_m=float(np.linalg.norm(after - before)),
                free_joint_names=[
                    sim.model.joint(j).name
                    for j in range(sim.model.njnt)
                    if sim.model.jnt_bodyid[j] == bid
                ],
                force_released=not op.runtime.interaction.owned_force.any(),
                lease_active=op.active(),
            )

    def run(args):
        args.output.mkdir(parents=True, exist_ok=True)
        report = dict(
            created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            mode="CPU WorldSim + released G1 policy + LocalBus",
            pose_boundary="Initial scene reset only; no qpos writes or pinning during any action",
            rendering=False,
            ports=False,
            cases=[],
        )
        report["git_head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        report["source_sha256"] = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                ROOT / "src/nerv/world/clearance.py",
                ROOT / "src/nerv/body/families/humanoid.py",
                ROOT / "src/nerv/world/mujoco_node.py",
                ROOT / "bodies/humanoid-unitree-g1/body.yaml",
                ROOT / "worlds/build/house-g1.xml",
                ROOT / "worlds/build/apt-g1.xml",
                ROOT / "policies/g1-29dof-turn/policy.onnx",
                ROOT / "policies/g1-29dof-turn/contract.json",
            ]
        }
        reg, spec = Registry(), Registry().body("humanoid-unitree-g1")
        for scene in ("house", "apt"):
            if args.case and not args.case.startswith(scene + "_"):
                continue
            world = reg.world(scene)
            sim = WorldSim(
                world.assets_root + "/" + world.supports[spec.name],
                physics=world.physics,
                layout=SceneLayout(world.assets_root, scene),
                spawn=world.spawn,
                body_defaults=_body_defaults_from_registry(spec),
            )
            body = None
            try:
                cases = authored_cases(sim, scene)
                body = HumanoidBody(spec.model_dump(), LocalBus(sim), SkillRunner(StopFlag()))
                foot_ids = feet_ids(sim, spec)
                sim.start()
                for case in cases:
                    if args.case and case["id"] != args.case:
                        continue
                    record = {**case, "passed": False}
                    report["cases"].append(record)
                    monitor = None
                    try:
                        # Only initial condition for this independent hazard case.
                        with sim._lock:
                            sim.spawn_pose = (
                                *case["xy"],
                                case.get("floor_z", 0.0) + spec.actuators["start_height"],
                                case["yaw"],
                            )
                            sim.reset()
                        body.release()
                        time.sleep(START_SETTLE_S)
                        if sim.status()["fallen"]:
                            raise RuntimeError("Initial standing pose did not stabilize")
                        record["static_before_preparation"] = static_check(
                            sim, body, case, foot_ids
                        )
                        if case.get("chair_body"):
                            record["chair_preparation"] = move_chair(sim, case)
                            if record["chair_preparation"]["displacement_m"] < MIN_APPROACH_M:
                                raise RuntimeError(
                                    "Chair did not physically move enough to test changed scenery"
                                )
                        record["static_before_action"] = static_check(sim, body, case, foot_ids)
                        record["before"] = sim.status()
                        monitor = Monitor(sim, body, case, foot_ids)
                        monitor.start()
                        record["action"] = body.invoke("move_forward", meters=WALK_REQUEST_M)
                        stop_t = float(sim.data.time)
                        time.sleep(AFTER_SETTLE_S)
                        monitor.stop()
                        if monitor.error:
                            raise RuntimeError("Hazard sampler failed: " + monitor.error)
                        record["after"] = sim.status()
                        record["static_after_action"] = static_check(sim, body, case, foot_ids)
                        samples = monitor.samples
                        final_samples = [
                            s for s in samples if s["t"] >= stop_t + AFTER_SETTLE_S / 2
                        ]
                        record["metrics"] = dict(
                            samples=len(samples),
                            min_feet_before_boundary_m=min(
                                s["feet_before_boundary_m"] for s in samples
                            ),
                            max_hazard_penetration_m=max(
                                s["hazard_penetration_m"] for s in samples
                            ),
                            max_tilt_deg=max(s["tilt_deg"] for s in samples),
                            min_root_z=min(s["base"]["z"] for s in samples),
                            fell=any(s["fallen"] for s in samples),
                            post_stop_max_speed_m_s=max(s["base_speed_m_s"] for s in final_samples),
                            policy_errors=sorted(
                                {s["policy_error"] for s in samples if s["policy_error"]}
                            ),
                        )
                        action, metrics = record["action"]["data"], record["metrics"]
                        record["passed"] = bool(
                            action["reason"] == "braked"
                            and action["moved_m"] >= MIN_APPROACH_M
                            and not metrics["fell"]
                            and not metrics["policy_errors"]
                            and metrics["min_feet_before_boundary_m"] >= -CONTACT_TOLERANCE_M
                            and metrics["max_hazard_penetration_m"] <= CONTACT_TOLERANCE_M
                            and metrics["post_stop_max_speed_m_s"]
                            <= sim.phys["scene_still_speed_m_s"]
                        )
                        record["renderer_initialized"] = sim.render._renderer is not None
                        dump(args.output / (case["id"] + "-samples.json"), samples)
                    except Exception as error:
                        if monitor and not monitor.done.is_set():
                            monitor.stop()
                        record["error"] = f"{type(error).__name__}: {error}"
                    dump(args.output / (case["id"] + ".json"), record)
                    dump(args.output / "report.json", report)
                    print(
                        json.dumps(
                            {
                                k: record[k]
                                for k in ("id", "passed", "metrics", "error", "action")
                                if k in record
                            }
                        ),
                        flush=True,
                    )
            finally:
                try:
                    if body:
                        body.close()
                finally:
                    sim.stop()
        report["passed"] = bool(report["cases"]) and all(c["passed"] for c in report["cases"])
        dump(args.output / "report.json", report)
        return report

    return run(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run one authored case ID; default runs all seven cases")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory; default is a fresh timestamped temp/acceptance/hazards run",
    )
    parser.add_argument("--repeats", type=positive_int, default=1)
    args = parser.parse_args()
    if args.output is None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        args.output = ROOT / "temp/acceptance/hazards" / stamp
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a new output directory to preserve earlier failures and evidence")
    output.mkdir(parents=True)
    os.environ["MUJOCO_GL"] = "disable"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["NERV_HOME"] = str(output / "nerv-home")
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "worlds")]
    records = []
    try:
        for repetition in range(1, args.repeats + 1):
            directory = output / f"repeat-{repetition:03d}"
            result = execute(argparse.Namespace(case=args.case, output=directory))
            records.append(
                dict(
                    repetition=repetition,
                    passed=result["passed"],
                    report=f"repeat-{repetition:03d}/report.json",
                )
            )
            if not result["passed"]:
                break
    finally:
        passed = len(records) == args.repeats and all(r["passed"] for r in records)
        (output / "report.json").write_text(
            json.dumps(
                dict(passed=passed, requested_repeats=args.repeats, repetitions=records), indent=2
            )
            + "\n"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
