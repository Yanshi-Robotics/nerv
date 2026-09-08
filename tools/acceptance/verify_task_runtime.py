"""Repeat the real fridge task while G1's released policy stands in WorldSim.

Runs no renderer, no second physics clock, no pose pinning and no external
service. Transport and image selection have separate acceptance tools.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import sys

import hashlib
import json
import time

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
    os.environ["MUJOCO_GL"] = "disable"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["NERV_HOME"] = str(output / "nerv-home")
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "worlds")]
    return output


HEARTBEAT_POLL_S = 0.1


def run(output, repeats):
    from nerv.platform.registry import Registry
    from nerv.world.mujoco_node import WorldSim, SceneLayout, _body_defaults_from_registry
    from nerv.body.families.humanoid import HumanoidBody
    from nerv.body.skills import SkillRunner, StopFlag
    from tools.benchmark_residences import LocalBus
    from tools.check_operator import perform_fridge_placement
    from scenes.explore import source_files, source_hash

    source_paths = [
        Path(__file__),
        ROOT / "src/nerv/world/mujoco_node.py",
        ROOT / "src/nerv/world/scene_operator.py",
        ROOT / "src/nerv/body/families/humanoid.py",
        ROOT / "bodies/humanoid-unitree-g1/body.yaml",
        ROOT / "policies/g1-29dof-turn/policy.onnx",
    ]
    metadata = dict(
        created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        source_sha256={
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_paths
        },
        world_source_hash=source_hash(source_files("apt")),
        requested_repeats=repeats,
    )
    reg = Registry()
    world = reg.world("apt")
    spec = reg.body("humanoid-unitree-g1")
    sim = WorldSim(
        world.assets_root + "/" + world.supports[spec.name],
        physics=world.physics,
        layout=SceneLayout(world.assets_root, "apt"),
        spawn=world.spawn,
        body_defaults=_body_defaults_from_registry(spec),
    )
    body = None
    records = []
    credentials = {}

    def step(seconds):
        """Yield the model lock to its existing loop; no second physics clock."""
        goal = float(sim.data.time) + seconds
        sim._lock.release()
        deadline = time.monotonic() + max(3, seconds * 3)
        try:
            while True:
                with sim._lock:
                    sim.scene_operator.check(**credentials, renew=True)
                    assert not sim.status()["fallen"], sim.status()
                    if sim.data.time >= goal:
                        break
                if time.monotonic() > deadline:
                    raise TimeoutError("The existing physics loop did not advance")
                time.sleep(min(HEARTBEAT_POLL_S, seconds))
        finally:
            sim._lock.acquire()

    try:
        body = HumanoidBody(spec.model_dump(), LocalBus(sim), SkillRunner(StopFlag()))
        sim.start()
        for repetition in range(repeats):
            sim.reset()
            body.release()
            time.sleep(3)
            with sim._lock:
                op = sim.scene_operator
                lease = op.acquire("task-runtime", "operator", sim.epoch)
                credentials = dict(
                    session="task-runtime", owner="operator", token=lease["token"], epoch=sim.epoch
                )
                assert not body._policy_error, body._policy_error
                before = sim.status()
                result = perform_fridge_placement(op.runtime, step)
                after = sim.status()
                assert not body._policy_error, body._policy_error
                records.append(
                    dict(
                        repetition=repetition + 1,
                        passed=True,
                        before=before,
                        after=after,
                        task=result,
                        policy_error=body._policy_error,
                    )
                )
                op.revoke("acceptance_complete")
            print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    finally:
        try:
            if body:
                body.close()
        finally:
            sim.stop()
        (output / "report.json").write_text(
            json.dumps(
                dict(**metadata, passed=len(records) == repeats, repetitions=records),
                ensure_ascii=False,
                indent=2,
            )
        )

    return 0 if len(records) == repeats else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=positive_int, default=5)
    args = parser.parse_args()
    return run(prepare(args.output), args.repeats)


if __name__ == "__main__":
    raise SystemExit(main())
