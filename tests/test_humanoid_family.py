"""Humanoid smoke: the G1 walks and turns in alice-house apt2 through the motor bus.

Needs the real assets, so it is gated on the environment:
  worlds/    the nerv-world submodule (arena build/apt2-g1.xml must exist)
  policies/  the nerv-policies submodule holding g1-29dof-turn/{policy.onnx,contract.json,release.yaml}
Otherwise it skips and says why. Measured numbers are printed (run with -s).
"""
from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("mujoco")
pytest.importorskip("onnxruntime")
pytest.importorskip("zmq")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORLD, BODY = "apt2", "humanoid-unitree-g1"
HEALTH_WAIT_S = 90.0          # the apt2 arena has ~3700 geoms; cold load + EGL takes a few seconds
STABILISE_S = 2.0             # let the policy loop stand the robot up before asking it to walk
MIN_MOVE_M = 0.5              # a 1 m walk must cover at least this
MIN_TURN_DEG = 20.0           # a 45° turn must measure at least this


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _skip_reason() -> str:
    from nerv import paths
    assets = os.path.join(paths.REPO_ROOT, "worlds")
    policies = os.path.join(paths.REPO_ROOT, "policies")
    if not assets:
        return "worlds/ submodule is not initialised"
    if not policies:
        return "policies/ submodule is not initialised"
    if not os.path.isfile(os.path.join(assets, "build", "apt2-g1.xml")):
        return f"arena {assets}/build/apt2-g1.xml does not exist"
    for fn in ("policy.onnx", "contract.json", "release.yaml"):
        if not os.path.isfile(os.path.join(policies, "g1-29dof-turn", fn)):
            return f"policy release {policies}/g1-29dof-turn lacks {fn}"
    return ""


@pytest.fixture(scope="module")
def humanoid():
    reason = _skip_reason()
    if reason:
        pytest.skip(f"humanoid smoke needs the real assets: {reason}")
    from nerv.body import families
    from nerv.body.bus_zmq import ZmqBus
    from nerv.body.skills import SkillRunner, StopFlag
    from nerv.platform.registry import Registry

    http_port, bus_port = _free_port(), _free_port()
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.setdefault("MUJOCO_GL", "egl")
    proc = subprocess.Popen([sys.executable, "-m", "nerv.world", "--world", WORLD, "--body", BODY,
                             "--http-port", str(http_port), "--bus-port", str(bus_port)],
                            env=env, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{http_port}"
    t0, ok = time.time(), False
    while time.time() - t0 < HEALTH_WAIT_S:
        if proc.poll() is not None:
            break
        try:
            r = httpx.get(base + "/health", timeout=1.0)
            if r.status_code == 200 and r.json().get("ok"):
                ok = True
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not ok:
        out = proc.stdout.read() if proc.poll() is not None else "(still running, no /health)"
        proc.kill()
        pytest.fail(f"world node did not come up: {out}")

    spec = Registry().body(BODY).model_dump()
    bus = ZmqBus(f"tcp://127.0.0.1:{bus_port}")
    body = families.load("humanoid").build(spec, bus, SkillRunner(StopFlag()))
    try:
        yield {"body": body, "http": base}
    finally:
        body.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _god(base: str) -> dict:
    return httpx.get(base + "/status", timeout=5).json()


def test_walk_and_turn(humanoid):
    body, base = humanoid["body"], humanoid["http"]
    time.sleep(STABILISE_S)
    st = body.status()
    assert not st["policy_error"], f"policy loop is failing: {st['policy_error']}"
    assert st["state"] and not st["state"]["fallen"], f"fell while standing: {st}"
    g0 = _god(base)
    print(f"\n[humanoid] standing at {g0.get('base')} room={g0.get('room_label')} tilt={g0.get('tilt_deg')}°")

    r = body.invoke("move_forward", meters=1.0)
    d = r["data"]
    print(f"[humanoid] move_forward(1.0): ok={r['ok']} moved={d['moved_m']} m reason={d['reason']} "
          f"fallen={d['fallen']} braked={d['braked']} stalled={d['stalled']} elapsed={d['elapsed_s']} s")
    print(f"[humanoid]   {r['message']}")
    assert not d["fallen"], r["message"]
    assert d["moved_m"] > MIN_MOVE_M, r["message"]

    r = body.invoke("turn_left", degrees=45)
    d = r["data"]
    print(f"[humanoid] turn_left(45): ok={r['ok']} turned={d['turned_deg']}° drift={d['moved_m']} m "
          f"reason={d['reason']} fallen={d['fallen']} elapsed={d['elapsed_s']} s")
    print(f"[humanoid]   {r['message']}")
    assert not d["fallen"], r["message"]
    assert d["turned_deg"] > MIN_TURN_DEG, r["message"]

    g1 = _god(base)
    print(f"[humanoid] now at {g1.get('base')} room={g1.get('room_label')} tilt={g1.get('tilt_deg')}°")
    dyaw = (g1["base"]["yaw_deg"] - g0["base"]["yaw_deg"] + 180) % 360 - 180
    print(f"[humanoid] god's-eye yaw change {dyaw:.1f}°, displacement "
          f"{math.hypot(g1['base']['x'] - g0['base']['x'], g1['base']['y'] - g0['base']['y']):.2f} m")

    state, images = body.observe()
    print(f"[humanoid] observation: {state} images={[(n, len(b)) for n, b in images]}")
    for key in ("x", "y", "room", "room_label", "odom_xy", "pose"):
        assert key not in state, "ground truth leaked into the observation"
    assert images and images[0][0] == "camera:head" and images[0][1][:8] == b"\x89PNG\r\n\x1a\n"
    tools = {t["name"] for t in body.tools()}
    assert tools == {"move_forward", "turn_left", "turn_right"}
