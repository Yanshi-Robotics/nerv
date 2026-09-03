"""Motor-bus round trip against a real world node hosting the SO-101 fixture (position actuators).

The node runs as a subprocess exactly the way the platform would launch it; the test talks to it
through the body's own ZmqBus client and the HTTP control plane.
"""
from __future__ import annotations

import io
import math
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("mujoco")
pytest.importorskip("zmq")

from nerv.body.bus_zmq import ZmqBus  # noqa: E402
from nerv.nerve.world import PD_POSITION_ACTUATOR, ActuatorSpec, BusCommand  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ARENA = os.path.join(HERE, "fixtures", "so101", "scene.xml")
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
HEALTH_WAIT_S = 30.0          # model load + EGL on a cold machine
SETTLE_S = 1.5                # wall seconds for the position actuators to track a small target


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def world():
    http_port, bus_port = _free_port(), _free_port()
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.setdefault("MUJOCO_GL", "egl")
    proc = subprocess.Popen([sys.executable, "-m", "nerv.world", "--arena", ARENA,
                             "--http-port", str(http_port), "--bus-port", str(bus_port)],
                            env=env, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{http_port}"
    t0 = time.time()
    ok = False
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
        time.sleep(0.2)
    if not ok:
        out = proc.stdout.read() if proc.poll() is not None else "(still running, no /health)"
        proc.kill()
        pytest.fail(f"world node did not come up: {out}")
    bus = ZmqBus(f"tcp://127.0.0.1:{bus_port}")
    try:
        yield {"http": base, "bus": bus, "proc": proc}
    finally:
        bus.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_spawn_write_read(world):
    bus: ZmqBus = world["bus"]
    info = bus.spawn(ActuatorSpec(joint_names=JOINTS, pd_mode=PD_POSITION_ACTUATOR))
    assert info["joint_names"] == JOINTS
    assert info["has_free_base"] is False
    limits = info["joint_limits"]
    assert len(limits) == len(JOINTS) and all(lo < hi for lo, hi in limits)
    assert info["dt"] > 0 and info["epoch"] == bus.epoch()

    st0 = bus.read()
    assert len(st0.joint_pos) == len(JOINTS) and len(st0.joint_vel) == len(JOINTS)
    assert st0.imu_quat == [] and st0.odom_xy == []

    # nudge shoulder_pan and elbow_flex towards a target inside their ranges
    target = list(st0.joint_pos)
    target[0] = st0.joint_pos[0] + 0.5
    target[2] = st0.joint_pos[2] - 0.4
    bus.write(BusCommand(targets=target))
    time.sleep(SETTLE_S)
    st1 = bus.read()
    assert st1.t > st0.t, "sim time must advance"
    for i in (0, 2):
        before = abs(st0.joint_pos[i] - target[i])
        after = abs(st1.joint_pos[i] - target[i])
        assert after < before * 0.5, f"{JOINTS[i]} did not move toward its target ({before:.3f} -> {after:.3f})"
    print(f"\n[world-bus] joints moved: {[round(v, 3) for v in st0.joint_pos]} -> {[round(v, 3) for v in st1.joint_pos]}")

    bus.reset()
    st2 = bus.read()
    assert abs(st2.joint_pos[0] - st0.joint_pos[0]) < 0.05


def test_sensors_and_frame(world):
    bus: ZmqBus = world["bus"]
    names = bus.sensors()
    assert "camera:wrist" in names and "camera:top" in names
    for name in ("camera:wrist", "top"):
        data, mime = bus.sensor(name)
        assert mime == "image/jpeg"
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        assert im.format == "JPEG" and im.size == (640, 480)
    with pytest.raises(Exception):
        bus.sensor("camera:does_not_exist")


def test_rays(world):
    bus: ZmqBus = world["bus"]
    angles = [0, 45, 90, 135, 180, 225, 270, 315]
    ranges = bus.rays(angles, 8.0)
    assert len(ranges) == len(angles)
    assert all(0.0 <= r <= 8.0 for r in ranges)
    assert all(math.isfinite(r) for r in ranges)


def test_http_control_plane(world):
    base = world["http"]
    h = httpx.get(base + "/health", timeout=5).json()
    assert h["ok"] and h["node"] == "world" and h["epoch"]
    s = httpx.get(base + "/status", timeout=5).json()
    assert "base" in s and "sim_time" in s and s["spawned"] is True
    lst = httpx.get(base + "/sensors", timeout=5).json()["sensors"]
    assert "camera:top" in lst
    r = httpx.get(base + "/sensors/camera:top", timeout=15)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert httpx.get(base + "/sensors/nope", timeout=5).status_code == 404
    assert httpx.post(base + "/reset", timeout=5).json()["ok"] is True
    assert httpx.get(base + "/", timeout=5).status_code == 200
