"""ZMQ client for the NERV/World motor bus (the simulation endpoint).

REQ/REP, one JSON object per message (see nerve.wire). A lock serialises callers: the policy
thread and the observation reader share one socket.
"""
from __future__ import annotations

import threading

import zmq

from ..nerve import wire
from ..nerve.world import (OP_CLOSE, OP_EPOCH, OP_RAYS, OP_READ, OP_RESET, OP_SENSOR, OP_SENSORS,
                           OP_SPAWN, OP_WRITE, ActuatorSpec, BusCommand, BusState)


class BusError(RuntimeError):
    pass


class ZmqBus:
    def __init__(self, url: str, timeout_ms: int = 5000) -> None:
        self.url = url
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.REQ)
        self._sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
        self._sock.setsockopt(zmq.SNDTIMEO, timeout_ms)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.connect(url)
        self._lock = threading.Lock()

    def _req(self, msg: dict) -> dict:
        with self._lock:
            try:
                self._sock.send(wire.dumps(msg))
                rep = wire.loads(self._sock.recv())
            except zmq.ZMQError as e:
                # A REQ socket that missed a reply is wedged; rebuild it.
                self._sock.close(0)
                self._sock = self._ctx.socket(zmq.REQ)
                self._sock.setsockopt(zmq.RCVTIMEO, 5000)
                self._sock.setsockopt(zmq.LINGER, 0)
                self._sock.connect(self.url)
                raise BusError(f"bus {msg.get('op')} failed: {e}") from e
        if not rep.get("ok", False):
            raise BusError(rep.get("error") or f"bus {msg.get('op')} refused")
        return rep

    def spawn(self, spec: ActuatorSpec) -> dict:
        return self._req({"op": OP_SPAWN, "joint_names": spec.joint_names, "pd_mode": spec.pd_mode,
                          "kp": spec.kp, "kd": spec.kd, "torque_limit": spec.torque_limit,
                          "default_pos": spec.default_pos})

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
        r = self._req({"op": OP_SENSOR, "name": name})
        return wire.b64d(r["data"]), r.get("mime", "image/jpeg")

    def rays(self, angles_deg: list[float], max_range_m: float) -> list[float]:
        return list(self._req({"op": OP_RAYS, "angles_deg": angles_deg,
                               "max_range_m": max_range_m}).get("ranges", []))

    def epoch(self) -> str:
        return str(self._req({"op": OP_EPOCH}).get("epoch", ""))

    def close(self) -> None:
        try:
            self._req({"op": OP_CLOSE})
        except Exception:
            pass
        self._sock.close(0)
