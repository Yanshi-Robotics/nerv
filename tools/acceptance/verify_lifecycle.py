"""Exercise lease expiry, cancel, reset, session replacement and owned-node stop.

Runs the real operator lifecycle against isolated simulation nodes, without
requesting model turns or using the resident hub. Reports transport-level cleanup.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import sys

import hashlib
import json
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_LEASE_S = 2.0  # The accepted operator expiry contract; never override the running lease.


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


def run(output, limit_s):
    OUT = output
    OUT.mkdir(parents=True, exist_ok=True)
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore

    START = time.monotonic()
    LIMIT_S = limit_s  # Per-repetition wall budget; each command checks this limit.
    POLL_S = 0.1
    SETTLE_S = 3
    hub = Nerv(store=SessionStore(str(OUT / "sessions")))
    report = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cpu_only": True,
        "cases": [],
        "force_damping_evidence": {
            "live_transport": "not_exposed",
            "source_review": "Interaction.cancel -> restore each passive joint damping; release -> clear only owned generalized force; SceneOperator.revoke/reset and WorldSim.stop invoke this path",
            "direct_force_arrays": "Not exposed by this transport; this tool records logical cleanup and owned process exit.",
        },
        "passed": False,
    }
    handles = []
    journal = (OUT / "events.jsonl").open("w")

    def event(kind, **values):
        record = {"elapsed_s": round(time.monotonic() - START, 3), "kind": kind, **values}
        journal.write(json.dumps(record, ensure_ascii=False) + "\n")
        journal.flush()
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def checked(result):
        assert result.get("ok"), result
        return result

    def poll(predicate, timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = client.scene_get("state")
            if predicate(value):
                return value
            time.sleep(POLL_S)
        raise AssertionError({"timeout": timeout, "last_state": value})

    class Owner:
        def __init__(self, sid):
            self.sid = sid
            self.sequence = 0
            lease = hub.scene_tests.acquire(sid, "lifecycle-" + str(time.time_ns()))
            self.credentials = {k: lease[k] for k in ("owner", "token", "epoch")}
            self.lease_s = lease["lease_seconds"]
            assert self.lease_s == EXPECTED_LEASE_S, (
                "Acceptance requires the two-second operator lease"
            )

        def command(self, action, **values):
            assert time.monotonic() - START < LIMIT_S, "acceptance wall limit"
            self.sequence += 1
            return checked(
                hub.scene_tests.action(
                    self.sid,
                    "command",
                    {**self.credentials, **values, "action": action, "sequence": self.sequence},
                )
            )

        def heartbeat(self):
            return checked(hub.scene_tests.action(self.sid, "heartbeat", self.credentials))

        def release(self):
            return checked(hub.scene_tests.action(self.sid, "release", self.credentials))

    def stale_rejected(owner, expected="Scene control expired or belongs to another operator"):
        try:
            result = hub.scene_tests.action(owner.sid, "heartbeat", dict(owner.credentials))
        except ValueError as exc:
            assert str(exc) == expected, str(exc)
            return True, f"ValueError: {exc}"
        # Network, HTTP and decoding errors propagate; they are not proof of rejection.
        assert result.get("ok") is False and result.get("message") == expected, result
        return True, result["message"]

    def activate(sid, name):
        owner = Owner(sid)
        before = client.scene_get("state")
        owner.command("focus", facility=door["id"])
        hit = owner.command("select", xy=[0, 0])["selection"]
        assert hit and door["joint"] in hit["names"], {"facility": door, "hit": hit}
        owner.command("joint", joint=door["joint"], fraction=1, xy=[0, 0])
        owner.command("focus", facility=can["id"])
        selected = owner.command("select", xy=[0, 0])["selection"]
        assert selected and selected["distance"] <= 2, selected
        grabbed = owner.command("grab", xy=[0, 0])
        assert grabbed["held"] is not None, grabbed
        owner.command("drag", xy=[0, 0], distance=max(0.15, selected["distance"] - 0.2))
        time.sleep(0.25)
        owner.heartbeat()
        active = client.scene_get("state")
        assert active["held"] is not None, active
        assert (
            active["joints"][door["joint"]]["fraction"]
            > before["joints"][door["joint"]]["fraction"]
        ), active["joints"][door["joint"]]
        state = client.status()
        assert not state.get("fallen"), state
        event(
            "activated",
            case=name,
            held=active["held"],
            door=active["joints"][door["joint"]],
            can=active["joints"][can["joint"]],
            robot=state,
        )
        return owner, active

    def reset(sid):
        checked(hub.reset_world(sid))
        time.sleep(SETTLE_S)
        status = client.status()
        assert not status.get("fallen"), status
        return client.scene_get("state")

    def cleaned(state):
        return state["held"] is None and all(
            s.get("result") != "moving" for s in state["joints"].values()
        )

    try:
        world = hub.registry.world("apt")
        body_spec = hub.registry.body("humanoid-unitree-g1")
        if world.kind != "sim" or world.url or body_spec.url:
            raise ValueError(
                "Acceptance requires local simulation descriptors without attached URLs"
            )
        session = hub.new_session("operator-validation", "humanoid-unitree-g1", "apt", tools=[])
        sid = session["id"]
        client = hub.world_client("apt", session["body"])
        handles = list(hub.launcher.nodes.values())
        assert handles and all(not h.attached and h.proc is not None for h in handles), (
            "Acceptance may control only its own launched nodes"
        )
        report["nodes"] = [
            {
                "key": h.name,
                "pid": h.proc.pid,
                "url": h.url,
                "bus": h.bus_url,
                "epoch": h.meta.get("epoch"),
            }
            for h in handles
        ]
        event("started", nodes=report["nodes"])
        time.sleep(SETTLE_S)
        catalogue = client.scene_get("catalogue")
        (OUT / "catalogue.json").write_text(json.dumps(catalogue, ensure_ascii=False, indent=2))
        facilities = catalogue["facilities"]
        door = next(f for f in facilities if f.get("joint", "").endswith("fridge_door_joint"))
        can = next(
            f for f in facilities if f["kind"] == "movable" and "can" in f.get("body", "").lower()
        )
        # Movable catalogue may expose only its body; operator state supplies its matching free joint name.
        initial = client.scene_get("state")
        if not can.get("joint"):
            can = {
                **can,
                "joint": next(
                    n
                    for n, v in initial["joints"].items()
                    if "can" in n.lower() and "position" in v
                ),
            }
        report["facilities"] = {"door": door, "can": can}
        (OUT / "initial.json").write_text(json.dumps(initial, ensure_ascii=False, indent=2))

        owner, active = activate(sid, "disconnect")
        interrupted = time.monotonic()
        done = poll(lambda s: not s["active"], owner.lease_s + 1)
        rejected, reason = stale_rejected(owner)
        assert done["reason"] == "lease_expired" and cleaned(done) and rejected, done
        case = {
            "name": "disconnect",
            "passed": True,
            "observed_cleanup_s": time.monotonic() - interrupted,
            "timing_scope": "GET state may itself expire a lease; not a direct autonomous-force timing measurement",
            "before": active,
            "after": done,
            "old_token_rejected": reason,
        }
        report["cases"].append(case)
        event(
            "case_pass",
            name="disconnect",
            seconds=case["observed_cleanup_s"],
            reason=done["reason"],
        )
        reset(sid)

        owner, active = activate(sid, "cancel")
        old_sequence = owner.sequence
        done = owner.command("cancel")
        assert done["active"] and cleaned(done), done
        owner.heartbeat()
        replay = hub.scene_tests.action(
            owner.sid,
            "command",
            {**owner.credentials, "sequence": old_sequence, "action": "grab", "xy": [0, 0]},
        )
        assert replay.get("ok") is False and replay.get("message") == (
            "Scene command was superseded by a later command"
        ), replay
        after_replay = client.scene_get("state")
        assert after_replay["active"] and cleaned(after_replay), after_replay
        owner.command("focus", facility=can["id"])
        report["cases"].append(
            {
                "name": "cancel",
                "passed": True,
                "before": active,
                "after": done,
                "stale_sequence_rejected": replay,
                "newer_sequence_accepted": True,
            }
        )
        event("case_pass", name="cancel", reason=done["reason"])
        owner.release()
        reset(sid)

        owner, active = activate(sid, "reset")
        # Populate a genuine task latch before checking the reset clears it.
        deadline = time.monotonic() + 4
        while (
            not active["task"].get("checks", {}).get("opened", False)
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
            owner.heartbeat()
            active = client.scene_get("state")
        assert active["held"] is not None and active["task"]["checks"]["opened"], active
        event(
            "reset_precondition",
            held=active["held"],
            door=active["joints"][door["joint"]],
            task=active["task"]["checks"],
        )
        done = reset(sid)
        rejected, reason = stale_rejected(owner)
        assert not done["active"] and cleaned(done) and rejected, done
        assert done["phase"] == initial["phase"]
        assert not done["task"].get("success") and not done["task"].get("checks", {}).get(
            "opened", False
        ), done["task"]
        free_errors = {
            n: max(abs(a - b) for a, b in zip(v["position"], initial["joints"][n]["position"]))
            for n, v in done["joints"].items()
            if "position" in v
        }
        assert max(free_errors.values()) < 0.025, (
            free_errors
        )  # 2.5 cm allows settled initial collision compliance.
        assert (
            abs(
                done["joints"][door["joint"]]["fraction"]
                - initial["joints"][door["joint"]]["fraction"]
            )
            < 0.04
        )
        report["cases"].append(
            {
                "name": "reset",
                "passed": True,
                "before": active,
                "after": done,
                "free_position_errors_m": free_errors,
                "old_token_rejected": reason,
            }
        )
        event("case_pass", name="reset", free_position_errors_m=free_errors)

        owner, active = activate(sid, "new_session_freezes_old")
        old_sid = sid
        session = hub.new_session("operator-validation", "humanoid-unitree-g1", "apt", tools=[])
        sid = session["id"]
        done = poll(lambda s: not s["active"])
        rejected, reason = stale_rejected(
            owner, "This session no longer controls a simulated world"
        )
        assert hub.store.get(old_sid).status == "frozen" and cleaned(done) and rejected
        report["cases"].append(
            {
                "name": "new_session_freezes_old",
                "passed": True,
                "before": active,
                "after": done,
                "old_token_rejected": reason,
                "old_session": old_sid,
                "new_session": sid,
            }
        )
        event("case_pass", name="new_session_freezes_old", old_token_rejected=reason)
        reset(sid)

        owner, active = activate(sid, "stop_simulation")
        body_key = "body:" + session["body"]
        body_handle = hub.launcher.nodes[body_key]
        stopped = checked(
            hub.stop_simulation(body_key, "apt", body_handle.meta["epoch"], session_id=sid)
        )
        rejected, reason = stale_rejected(
            owner, "This session no longer controls a simulated world"
        )
        assert hub.store.get(sid).status == "frozen"
        assert all(h.proc.poll() is not None for h in handles) and rejected
        report["cases"].append(
            {
                "name": "stop_simulation",
                "passed": True,
                "before": active,
                "result": stopped,
                "old_token_rejected": reason,
                "owned_processes_exited": [
                    {"pid": h.proc.pid, "returncode": h.proc.poll()} for h in handles
                ],
                "force_evidence": "No live old world remains; pre-exit revoke path source-reviewed, force arrays not exposed via this transport.",
            }
        )
        event("case_pass", name="stop_simulation", result=stopped)
        report["passed"] = len(report["cases"]) == 5
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        hub.shutdown()
        report["cleanup"] = [
            {"key": h.name, "pid": h.proc.pid, "returncode": h.proc.poll()} for h in handles
        ]
        report["duration_s"] = round(time.monotonic() - START, 3)
        source_paths = [
            "src/nerv/world/scene_operator.py",
            "src/nerv/world/mujoco_node.py",
            "src/nerv/platform/scene_tests.py",
            "src/nerv/platform/hub.py",
            "worlds/scenes/interaction.py",
            "worlds/build/apt-g1.xml",
            "bodies/humanoid-unitree-g1/body.yaml",
        ]
        report["source_sha256"] = {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_paths
        }
        (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        event(
            "finished",
            passed=report["passed"],
            duration_s=report["duration_s"],
            cleanup=report["cleanup"],
        )
        journal.close()

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=positive_int, default=1)
    parser.add_argument(
        "--limit-seconds",
        type=positive_int,
        default=500,
        help="Wall budget for each repetition (default: 500)",
    )
    args = parser.parse_args()
    output = prepare(args.output)
    from nerv import paths

    paths.LOGS_DIR = str(output / "logs")
    os.environ["NERV_TRUST_ALL"] = "1"
    records = []
    try:
        for repetition in range(1, args.repeats + 1):
            result = run(output / f"repeat-{repetition:03d}", args.limit_seconds)
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
