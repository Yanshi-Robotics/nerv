"""Follow residence routes through an isolated NERV safety gate, MCP and ZMQ chain.

Uses the released G1 policy and source-defined routes. Does not request model
turns or camera observations. Starts and closes only its own simulation nodes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "worlds"))

GOAL_TOLERANCE_M = 0.18  # Remain near the authored centreline before turning near furnishings.
MIN_COMMAND_M = 0.05  # Smallest useful correction with the released walking gait.
MIN_PROGRESS_M = 0.02  # Distinguish a physical stop from reaching the requested waypoint.
MIN_TURN_PROGRESS_DEG = 1.0
STOP_ESTIMATE_WEIGHT = 0.5  # Adapt to measured settling travel; never alter gait parameters.
settling_travel_m = 0.0
HEADING_TOLERANCE_DEG = 4.0  # Correct heading before each bounded forward move.
MAX_COMMAND_M = 2.0  # Frequent operator checks along the source-defined route.
MAX_LEG_ACTIONS = 60  # A bounded failure, never an endless attempt to reach a goal.
SETTLE_S = 3.0  # Initial stand stabilization before the first operator command.
PATH_SAMPLE_M = 1.5  # Correct lateral drift along narrow pool paths, not only at distant corners.


class OperatorOnly:
    name, model, vision = "operator-validation", "no-model", False

    def run_turn(self, *args):
        raise RuntimeError("This acceptance run must not invoke a model")


def pose(client):
    status = client.status()
    if status.get("fallen"):
        raise RuntimeError(f"Robot fell: {status}")
    return status["base"], status


def execute(hub, sid, name, arguments, journal):
    result = None
    started = time.monotonic()
    for event in hub.teleop_stream(sid, name, arguments):
        if event.get("type") == "tool_result":
            result = event
    if result is None:
        raise RuntimeError("The real control chain did not return a tool result")
    record = dict(
        action=name, arguments=arguments, result=result, wall_seconds=time.monotonic() - started
    )
    journal.write(json.dumps(record, ensure_ascii=False) + "\n")
    journal.flush()
    if not result.get("ok"):
        raise RuntimeError(f"Action refused or failed: {result}")
    return result


def reach(hub, sid, client, goal, journal):
    global settling_travel_m
    for _ in range(MAX_LEG_ACTIONS):
        p, status = pose(client)
        dx, dy = goal[0] - p["x"], goal[1] - p["y"]
        distance = math.hypot(dx, dy)
        if distance <= GOAL_TOLERANCE_M:
            return dict(goal=goal, actual=p, error_m=distance, tilt_deg=status["tilt_deg"])
        wanted = math.degrees(math.atan2(dy, dx))
        delta = (wanted - p["yaw_deg"] + 180) % 360 - 180
        before = p
        turning = abs(delta) > HEADING_TOLERANCE_DEG
        if turning:
            execute(
                hub,
                sid,
                "turn_left" if delta > 0 else "turn_right",
                {"degrees": abs(delta)},
                journal,
            )
        else:
            request_m = max(MIN_COMMAND_M, min(MAX_COMMAND_M, distance - settling_travel_m))
            execute(hub, sid, "move_forward", {"meters": request_m}, journal)
        after, status = pose(client)
        remaining = math.hypot(goal[0] - after["x"], goal[1] - after["y"])
        travelled = (after["x"] - before["x"]) * math.cos(math.radians(before["yaw_deg"])) + (
            after["y"] - before["y"]
        ) * math.sin(math.radians(before["yaw_deg"]))
        turned = abs((after["yaw_deg"] - before["yaw_deg"] + 180) % 360 - 180)
        wanted_after = math.degrees(math.atan2(goal[1] - after["y"], goal[0] - after["x"]))
        heading_error_after = abs((wanted_after - after["yaw_deg"] + 180) % 360 - 180)
        if not turning and travelled >= request_m:
            settling_travel_m = (
                1 - STOP_ESTIMATE_WEIGHT
            ) * settling_travel_m + STOP_ESTIMATE_WEIGHT * (travelled - request_m)
        journal.write(
            json.dumps(
                dict(
                    goal=goal,
                    before=before,
                    after=after,
                    remaining_m=remaining,
                    heading_error_after_deg=heading_error_after,
                    travelled_m=travelled,
                    turned_deg=turned,
                    settling_estimate_m=settling_travel_m,
                )
            )
            + "\n"
        )
        journal.flush()
        if remaining > GOAL_TOLERANCE_M:
            # A small final correction is useful if it satisfies the existing heading tolerance.
            if (
                turning
                and turned < MIN_TURN_PROGRESS_DEG
                and heading_error_after > HEADING_TOLERANCE_DEG
            ):
                raise RuntimeError(f"Turn made no useful progress: {status}")
            if not turning and travelled < MIN_PROGRESS_M:
                raise RuntimeError(f"Forward motion stopped before target: {status}")
    raise RuntimeError(f"Goal not reached after {MAX_LEG_ACTIONS} actions: {goal}")


def routes_for(scene):
    from scenes.manifest import load_layout
    from scenes.paths import residence_routes

    items = {item["id"]: item["points"] for item in residence_routes(load_layout(scene))}
    if scene == "apt":
        entry = list(reversed(items["entry_gallery"]))
        return {
            name: entry if name == "entry_gallery" else entry + points[1:]
            for name, points in items.items()
        }
    entry = items["gate_approach"][:-2]
    routes = {"gate_approach": items["gate_approach"]}
    for name in ("pool_approach", "lawn_approach", "pool_loop", "residence_loop"):
        routes[name] = entry + items[name]
    routes["rear_branch"] = entry + items["residence_loop"][:3] + items["rear_branch"]
    return routes


def sample_path(points):
    result = [points[0]]
    for start, end in zip(points, points[1:]):
        count = max(1, math.ceil(math.dist(start[:2], end[:2]) / PATH_SAMPLE_M))
        result.extend(
            [
                [a + (b - a) * index / count for a, b in zip(start, end)]
                for index in range(1, count + 1)
            ]
        )
    return result


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scene", choices=("apt", "house"), required=True)
    ap.add_argument("--repeats", type=positive_int, default=1)
    ap.add_argument("--route")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output = args.output.resolve()
    if args.output.exists():
        ap.error("Use a new output directory to preserve earlier failures and evidence")
    args.output.mkdir(parents=True)
    os.environ["MUJOCO_GL"] = "disable"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["NERV_HOME"] = str(args.output / "nerv-home")
    routes = routes_for(args.scene)
    if args.route:
        requested = set(args.route.split(","))
        unknown = requested - set(routes)
        if unknown:
            ap.error("Unknown route(s): " + ", ".join(sorted(unknown)))
    from nerv import paths

    paths.LOGS_DIR = str(args.output / "logs")
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore

    os.environ["NERV_TRUST_ALL"] = "1"  # Only this isolated operator acceptance process.
    hub = Nerv(store=SessionStore(str(args.output / "sessions")))
    hub._brains[OperatorOnly.name] = OperatorOnly()
    hub._brains["operator"] = OperatorOnly()
    report = dict(scene=args.scene, repetitions=args.repeats, passed=False, routes=[])
    source_paths = [
        Path(__file__),
        ROOT / "src/nerv/body/families/humanoid.py",
        ROOT / "src/nerv/world/clearance.py",
        ROOT / "src/nerv/world/mujoco_node.py",
        ROOT / "bodies/humanoid-unitree-g1/body.yaml",
        ROOT / "worlds/scenes/paths.py",
        ROOT / f"worlds/build/{args.scene}-g1.xml",
    ]
    report["source_sha256"] = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths
    }
    report["MUJOCO_GL"] = os.environ.get("MUJOCO_GL")
    try:
        world = hub.registry.world(args.scene)
        body_spec = hub.registry.body("humanoid-unitree-g1")
        if world.kind != "sim" or world.url or body_spec.url:
            raise ValueError(
                "Acceptance requires local simulation descriptors without attached URLs"
            )
        session = hub.new_session(OperatorOnly.name, "humanoid-unitree-g1", args.scene, tools=[])
        assert hub.launcher.nodes and all(
            not h.attached and h.proc is not None for h in hub.launcher.nodes.values()
        ), "Acceptance may control only its own launched nodes"
        sid = session["id"]
        client = hub.world_client(args.scene, session["body"])
        report["session"] = sid
        time.sleep(SETTLE_S)
        assert hub.arm(sid, True)["ok"]
        for route_name, waypoints in routes.items():
            if args.route and route_name not in args.route.split(","):
                continue
            for repetition in range(args.repeats):
                print(f"{args.scene}/{route_name} repetition {repetition + 1}", flush=True)
                record = dict(name=route_name, repetition=repetition + 1, reached=[])
                report["routes"].append(record)
                with (args.output / f"{route_name}-{repetition + 1}.jsonl").open("w") as journal:
                    start, _ = pose(client)
                    full = sample_path(list(waypoints) + list(reversed(waypoints[:-1])))
                    for target in full:
                        actual = reach(hub, sid, client, target, journal)
                        record["reached"].append(actual)
                        print(actual, flush=True)
                    record["returned"] = reach(hub, sid, client, [start["x"], start["y"]], journal)
                record["passed"] = True
        report["passed"] = bool(report["routes"]) and all(r.get("passed") for r in report["routes"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        hub.shutdown()
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
