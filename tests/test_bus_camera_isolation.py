"""Real ZMQ round trips with a blocked sensor: no sockets listen on TCP ports."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from nerv.body.bus_zmq import ZmqBus, BusError
from nerv.body.families.humanoid import HumanoidBody
from nerv.nerve import wire
from nerv.nerve.world import BusCommand, BusState
from nerv.world.bus_server import BusServer


@pytest.fixture
def delayed_bus():
    entered, release = threading.Event(), threading.Event()
    writes = []
    def handler(msg):
        if msg["op"] == "sensor":
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release the slow camera")
            return {"data": wire.b64e(b"frame"), "mime": "image/jpeg"}
        if msg["op"] == "read":
            return {"t": time.monotonic(), "joint_pos": [.1], "joint_vel": [0.]}
        if msg["op"] == "write":
            writes.append(msg["targets"])
        return {}
    url = "inproc://camera-isolation-" + uuid.uuid4().hex
    server = BusServer(url, handler)
    server.start()
    bus = ZmqBus(url, timeout_ms=1000)
    try:
        yield bus, server, entered, release, writes
    finally:
        release.set()
        bus.close()
        server.stop()


def test_slow_camera_does_not_block_policy_motor_roundtrips_or_emergency_hold(delayed_bus):
    bus, _, entered, release, writes = delayed_bus
    with ThreadPoolExecutor(max_workers=1) as pool:
        frame = pool.submit(bus.sensor, "head")
        assert entered.wait(1)
        # Actual humanoid emergency-latch path writes its standing targets through
        # the same bus instance that is still waiting for the camera frame.
        body = HumanoidBody.__new__(HumanoidBody)
        body.bus, body._hold_lock = bus, threading.Lock()
        body._held, body._hold_pending, body._last_targets = False, None, [.25]
        body._state = BusState(0, [.1], [0.])
        stopped = threading.Event()
        body.runner = SimpleNamespace(stop=SimpleNamespace(request=stopped.set))
        body._set_command = lambda *args: None
        before = time.monotonic()
        for _ in range(20):
            assert bus.read().joint_pos == [.1]
            bus.write(BusCommand(targets=[.2]))
        held = body._hold("operator")
        elapsed = time.monotonic() - before
        assert elapsed < .5, f"motor/stop traffic waited {elapsed:.3f}s for the camera"
        assert held["held"] and stopped.is_set() and writes[-1] == [.25]
        assert not frame.done() and not release.is_set()
        release.set()
        assert frame.result(timeout=1) == (b"frame", "image/jpeg")


def test_busy_camera_is_bounded_and_does_not_block_another_client(delayed_bus):
    bus, _, entered, release, _ = delayed_bus
    other = ZmqBus(bus.url, timeout_ms=500)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            frame = pool.submit(bus.sensor, "head")
            assert entered.wait(1)
            with pytest.raises(BusError, match="camera busy"):
                other.sensor("head")
            assert other.read().joint_pos == [.1]
            release.set()
            frame.result(timeout=1)
    finally:
        other.close()


def test_sensor_timeout_recovers_with_original_timeout_and_motor_still_works(delayed_bus):
    _, server, entered, release, _ = delayed_bus
    bus = ZmqBus(server.bind_url, timeout_ms=40)
    try:
        with pytest.raises(BusError):
            bus.sensor("head")
        assert entered.is_set()
        assert bus.read().joint_pos == [.1]
        assert bus._camera._sock.getsockopt(__import__("zmq").RCVTIMEO) == 40
        assert bus._camera._sock.getsockopt(__import__("zmq").SNDTIMEO) == 40
        release.set()
        deadline = time.monotonic() + 1
        while True:
            try:
                assert bus.sensor("head")[0] == b"frame"
                break
            except BusError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        bus.close()
