"""Capture real four-phase native cameras with byte-identical physics state at each switch.

Uses WorldSim's render thread and released G1 standing policy. Starts no network
services, requests no model turns and never pins the robot. Requires a GPU queue reservation.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import sys

import hashlib
import importlib
import json
import math
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def prepare(output):
    output = output.resolve()
    if output.exists():
        raise ValueError("Use a new output directory to preserve earlier failures and evidence")
    output.mkdir(parents=True)
    os.environ["MUJOCO_GL"] = "egl"
    os.environ["NERV_HOME"] = str(output / "nerv-home")
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "worlds")]
    return output


SETTLE_S = 3.0
HEARTBEAT_S = 0.3


def execute(output, scenes, queue_id):
    import numpy as np
    from PIL import Image
    from nerv.platform.registry import Registry
    from nerv.world.mujoco_node import WorldSim, SceneLayout, _body_defaults_from_registry
    from nerv.body.families.humanoid import HumanoidBody
    from nerv.body.skills import SkillRunner, StopFlag
    from tools.benchmark_residences import LocalBus
    from scenes.explore import source_files, source_hash

    def sha(x):
        return hashlib.sha256(x if isinstance(x, bytes) else np.asarray(x).tobytes()).hexdigest()

    def write(obj, output):
        (output / "report.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2))

    def physical(sim):
        d, m = sim.data, sim.model
        names = (
            "qpos",
            "qvel",
            "qacc",
            "qacc_warmstart",
            "act",
            "ctrl",
            "qfrc_applied",
            "xfrc_applied",
            "mocap_pos",
            "mocap_quat",
        )
        result = {k: sha(getattr(d, k)) for k in names}
        result["time"] = float(d.time)
        result["model_physics"] = {
            k: sha(getattr(m, k))
            for k in (
                "body_mass",
                "body_inertia",
                "geom_contype",
                "geom_conaffinity",
                "geom_friction",
                "jnt_range",
                "dof_damping",
            )
        }
        result["epoch"] = sim.epoch
        return result

    def view_from_shot(eye, target):
        eye, target = np.array(eye), np.array(target)
        v = target - eye
        # A nearer look-at preserves the exact authored camera ray while staying
        # inside the existing operator-camera distance bounds.
        distance = min(8.0, float(np.linalg.norm(v)))
        return dict(
            lookat=(eye + v / np.linalg.norm(v) * distance).tolist(),
            distance=distance,
            azimuth=math.degrees(math.atan2(v[1], v[0])),
            elevation=math.degrees(math.atan2(v[2], np.linalg.norm(v[:2]))),
        )

    def run(output, scenes, queue_id):
        OUT = output
        OUT.mkdir(parents=True, exist_ok=True)
        report = dict(
            created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            queue=queue_id,
            mode="Actual WorldSim, released G1 standing policy, LocalBus, production scene_command/render thread",
            ports=False,
            resident_access=False,
            pinning=False,
            scenes=[],
            passed=False,
        )
        paths = [
            ROOT / "src/nerv/world/mujoco_node.py",
            ROOT / "src/nerv/world/scene_operator.py",
            ROOT / "bodies/humanoid-unitree-g1/body.yaml",
            ROOT / "worlds/scenes/operator.py",
            ROOT / "worlds/scenes/time_cycle.py",
            ROOT / "worlds/scenes/apply_time_preset.py",
            ROOT / "policies/g1-29dof-turn/policy.onnx",
        ]
        for scene in scenes:
            paths += [
                ROOT / f"worlds/build/{scene}-g1.xml",
                ROOT / f"worlds/scenes/{scene}/layout.py",
                ROOT / f"worlds/scenes/{scene}/shots.py",
            ]
        report["source_sha256"] = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in paths}
        report["world_source_hash"] = {scene: source_hash(source_files(scene)) for scene in scenes}
        reg = Registry()
        spec = reg.body("humanoid-unitree-g1")
        for scene in scenes:
            world = reg.world(scene)
            sim = WorldSim(
                world.assets_root + "/" + world.supports[spec.name],
                physics=world.physics,
                layout=SceneLayout(world.assets_root, scene),
                spawn=world.spawn,
                body_defaults=_body_defaults_from_registry(spec),
            )
            body = None
            stop = threading.Event()
            hb = None
            rec = dict(
                scene=scene, phases=[], mutations=[], frames=[], camera_views={}, passed=False
            )
            report["scenes"].append(rec)
            write(report, OUT)
            try:
                body = HumanoidBody(spec.model_dump(), LocalBus(sim), SkillRunner(StopFlag()))
                sim.start()
                body.release()
                time.sleep(SETTLE_S)
                # Initialize the real renderer before obtaining the short operator lease.
                sensor = sim.sensor_names()[0]
                sim.render_camera(sensor)
                with sim._lock:
                    lease = sim.scene_operator.acquire(
                        "time-validation", "time-validation", sim.epoch
                    )
                    credentials = dict(
                        session="time-validation",
                        owner="time-validation",
                        token=lease["token"],
                        epoch=sim.epoch,
                    )

                def renew():
                    while not stop.wait(HEARTBEAT_S):
                        try:
                            with sim._lock:
                                sim.scene_operator.check(**credentials, renew=True)
                        except Exception as e:
                            rec.setdefault("heartbeat_errors", []).append(str(e))
                            return

                hb = threading.Thread(target=renew, daemon=True)
                hb.start()
                original = sim.scene_operator.runtime.set_time

                def measured(phase, renderer=None):
                    # scene_command already holds the production physics lock here.
                    before = physical(sim)
                    oldtex = sha(sim.model.tex_data)
                    warnings = original(phase, renderer)
                    after = physical(sim)
                    rec["mutations"].append(
                        dict(
                            phase=phase,
                            thread=threading.current_thread().name,
                            physics_before=before,
                            physics_after=after,
                            unchanged=before == after,
                            texture_before=oldtex,
                            texture_after=sha(sim.model.tex_data),
                            warnings=warnings,
                            renderer_context=id(renderer),
                        )
                    )
                    return warnings

                sim.scene_operator.runtime.set_time = measured
                shots = importlib.import_module("scenes." + scene + ".shots")
                if scene == "house":
                    wanted = {"X2": "entry", "X4": "walkway", "X5": "pool", "X7": "gate"}
                    views = {
                        wanted[x[0]]: view_from_shot(x[2], x[3])
                        for x in shots.EXTERIOR
                        if x[0] in wanted
                    }
                    from scenes.paths import residence_routes

                    layout = importlib.import_module("scenes." + scene + ".layout")
                    routes = {route["id"]: route["points"] for route in residence_routes(layout)}
                    # Human-height cameras along the actual authored traversable routes.
                    for label, key, segment, fraction in [
                        ("west_walk", "residence_loop", 1, 0.0),
                        ("entry_to_gate", "gate_approach", 5, 0.0),
                        ("inside_gate", "gate_approach", 6, 0.75),
                        ("west_pool", "pool_approach", 1, 0.0),
                    ]:
                        a, b = (
                            np.asarray(routes[key][segment], dtype=float),
                            np.asarray(routes[key][segment + 1], dtype=float),
                        )
                        a = np.r_[a[:2], 0.0] if len(a) == 2 else a
                        b = np.r_[b[:2], 0.0] if len(b) == 2 else b
                        delta = b - a
                        a = a + fraction * delta
                        direction = delta / np.linalg.norm(delta)
                        eye = a + np.array([0.0, 0.0, 1.65])
                        target = (
                            a
                            + direction * min(8.0, np.linalg.norm(b - a))
                            + np.array([0.0, 0.0, 0.5])
                        )
                        views[label] = view_from_shot(eye, target)

                else:
                    x = next(x for x in shots.EYE if x[0] == "V2")
                    eye = x[2]
                    target = np.array(eye) + np.array(
                        [math.cos(math.radians(x[3])), math.sin(math.radians(x[3])), 0.0]
                    )
                    views = {"living": view_from_shot(eye, target)}
                rec["camera_views"] = views

                def save(view, phase, rgb, t):
                    directory = OUT / scene / view
                    directory.mkdir(parents=True, exist_ok=True)
                    path = directory / (phase + ".png")
                    Image.fromarray(rgb).save(path)
                    lum = np.asarray(rgb, dtype=float).mean(axis=2)
                    frame = dict(
                        view=view,
                        phase=phase,
                        path=str(path.relative_to(OUT)),
                        sim_time=t,
                        pixel_sha256=sha(rgb),
                        file_sha256=sha(path.read_bytes()),
                        mean_luminance=float(lum.mean()),
                        std_luminance=float(lum.std()),
                        fraction_over_20=float((lum > 20).mean()),
                    )
                    rec["frames"].append(frame)

                sequence = 0
                for phase in sim.scene_operator.runtime.phases:
                    sequence += 1
                    result = sim.scene_command(
                        dict(action="time", phase=phase, sequence=sequence, **credentials)
                    )
                    rec["phases"].append(
                        dict(
                            requested=phase,
                            reported=result.get("phase"),
                            fallen=sim.status()["fallen"],
                        )
                    )
                    for name in ("head", "chase"):
                        rgb, t = sim.render_camera(sensor) if name == "head" else sim.render_chase()
                        save(name, phase, rgb, t)
                    for name, values in views.items():
                        sequence += 1
                        sim.scene_command(
                            dict(action="view", sequence=sequence, **values, **credentials)
                        )
                        rgb, t = sim.render_scene_operator(credentials)
                        save(name, phase, rgb, t)
                    print(scene, phase, "captured", sim.status()["fallen"], flush=True)
                    write(report, OUT)
                rec["unique_per_view"] = {
                    view: len({f["pixel_sha256"] for f in rec["frames"] if f["view"] == view})
                    for view in ("head", "chase", *views)
                }
                rec["status_after"] = sim.status()
                rec["policy_error"] = body._policy_error
                rec["passed"] = (
                    all(
                        x["unchanged"] and x["thread"] == "nerv-world-render"
                        for x in rec["mutations"]
                    )
                    and all(n == 4 for n in rec["unique_per_view"].values())
                    and all(
                        not phase["fallen"] and phase["reported"] == phase["requested"]
                        for phase in rec["phases"]
                    )
                    and not rec.get("heartbeat_errors")
                    and not rec["status_after"]["fallen"]
                    and not rec["policy_error"]
                )
            except Exception:
                rec["error"] = traceback.format_exc()
                print(rec["error"], flush=True)
            finally:
                stop.set()
                if hb:
                    hb.join(timeout=1)
                try:
                    if body:
                        body.close()
                finally:
                    sim.stop()
                    rec["owned_instance_stopped"] = True
                    write(report, OUT)
        report["passed"] = all(x["passed"] for x in report["scenes"])
        report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write(report, OUT)
        return report

    return run(output, scenes, queue_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenes", nargs="+", choices=("house", "apt"), default=["house", "apt"])
    parser.add_argument("--repeats", type=positive_int, default=1)
    parser.add_argument(
        "--queue-id",
        default=os.environ.get("NERV_GPU_WINDOW"),
        help="Existing GPU queue reservation ID (or NERV_GPU_WINDOW)",
    )
    args = parser.parse_args()
    if not args.queue_id or not args.queue_id.strip():
        parser.error("An existing GPU queue reservation is required via --queue-id or NERV_GPU_WINDOW")
    if len(set(args.scenes)) != len(args.scenes):
        parser.error("Each scene may appear only once; use --repeats for repeated validation")
    output = prepare(args.output)
    records = []
    try:
        for repetition in range(1, args.repeats + 1):
            result = execute(output / f"repeat-{repetition:03d}", args.scenes, args.queue_id)
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
