"""ZMQ client for the NERV/World motor bus (the simulation endpoint).

REQ messages, one JSON object per message (see nerve.wire). Motor and camera requests
use independent channels so a slow frame cannot hold the policy or emergency-stop lock.
"""
from __future__ import annotations

import threading

import zmq

from ..nerve import wire
from ..nerve.world import (OP_CLEARANCE, OP_CLOSE, OP_EPOCH, OP_RAYS, OP_READ, OP_RESET, OP_SENSOR, OP_SENSORS,
                           OP_SPAWN, OP_WRITE, ActuatorSpec, BusCommand, BusState)


class BusError(RuntimeError):
    pass


class _RequestChannel:
    """Serialized REQ exchange, with a fresh socket after a missed reply."""
    def __init__(self, url: str, timeout_ms: int) -> None:
        self.url, self.timeout_ms = url, timeout_ms
        self._ctx = zmq.Context.instance()
        self._lock = threading.Lock()
        self._closed = False
        self._sock = self._connect()

    def _connect(self):
        sock = self._ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        sock.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self.url)
        return sock

    def request(self, msg: dict) -> dict:
        with self._lock:
            if self._closed:
                raise BusError("bus is closed")
            try:
                self._sock.send(wire.dumps(msg))
                rep = wire.loads(self._sock.recv())
            except zmq.ZMQError as error:
                self._sock.close(0)
                self._sock = self._connect()
                raise BusError(f"bus {msg.get('op')} failed: {error}") from error
        if not rep.get("ok", False):
            raise BusError(rep.get("error") or f"bus {msg.get('op')} refused")
        return rep

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._sock.close(0)


class ZmqBus:
    def __init__(self, url: str, timeout_ms: int = 5000) -> None:
        self.url = url
        self._motor = _RequestChannel(url, timeout_ms)
        self._camera = _RequestChannel(url, timeout_ms)

    def _req(self, msg: dict) -> dict:
        return self._motor.request(msg)

    def spawn(self, spec: ActuatorSpec) -> dict:
        return self._req({"op": OP_SPAWN, "joint_names": spec.joint_names, "pd_mode": spec.pd_mode,
                          "kp": spec.kp, "kd": spec.kd, "torque_limit": spec.torque_limit,
                          "default_pos": spec.default_pos, "extra": dict(spec.extra or {})})

    def read(self) -> BusState:
        r = self._req({"op": OP_READ})
        return BusState(t=float(r["t"]), joint_pos=r["joint_pos"], joint_vel=r["joint_vel"],
                        imu_quat=r.get("imu_quat", []), imu_gyro=r.get("imu_gyro", []),
                        imu_acc=r.get("imu_acc", []), odom_xy=r.get("odom_xy", []),
                        odom_yaw=float(r.get("odom_yaw", 0.0)),
                        base_height=float(r.get("base_height", 0.0)), extra=r.get("extra", {}))

    def write(self, cmd: BusCommand) -> None:
        msg = {"op": OP_WRITE, "targets": cmd.targets}
        if cmd.kp is not None:
            msg["kp"] = cmd.kp
        if cmd.kd is not None:
            msg["kd"] = cmd.kd
        self._req(msg)

    def reset(self) -> None:
        self._req({"op": OP_RESET})

    def sensors(self) -> list[str]:
        return list(self._req({"op": OP_SENSORS}).get("sensors", []))

    def sensor(self, name: str) -> tuple[bytes, str]:
        r = self._camera.request({"op": OP_SENSOR, "name": name})
        return wire.b64d(r["data"]), r.get("mime", "image/jpeg")

    def rays(self, angles_deg: list[float], max_range_m: float) -> list[float]:
        return list(self._req({"op": OP_RAYS, "angles_deg": angles_deg,
                               "max_range_m": max_range_m}).get("ranges", []))

    def clearance(self, query: dict) -> dict:
        return self._req({"op": OP_CLEARANCE, "query": query})

    def epoch(self) -> str:
        return str(self._req({"op": OP_EPOCH}).get("epoch", ""))

    def close(self) -> None:
        try:
            self._req({"op": OP_CLOSE})
        except Exception:
            pass
        self._motor.close()
        self._camera.close()
