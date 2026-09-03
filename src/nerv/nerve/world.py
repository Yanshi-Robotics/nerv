"""NERV/World — what sits between a body and the world it stands in.

Data plane, spoken by the body node (System 1, at policy rate):
  the **motor bus**: read joint state / IMU / odometry, write joint targets and gains.
  Shaped after Unitree LowCmd/LowState and LeRobot send_action/get_observation, so a
  simulated world and a real motor controller are interchangeable endpoints.
Data plane, readable by the platform too: **sensor streams** mounted in the world
  (an overhead camera, a room rangefinder) — sensors only, never ground truth.
Control plane, platform and operator only: launch, /health, spawn, /reset, /status
  (ground truth), /stream (chase camera), epoch.

Transport for simulation: ZMQ REQ/REP with JSON messages (images base64) on the bus
socket; HTTP for the control plane. For hardware the bus endpoint is the driver itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# ---- bus messages (JSON on the wire) ----------------------------------------------------

OP_SPAWN = "spawn"
OP_READ = "read"
OP_WRITE = "write"
OP_RESET = "reset"
OP_SENSORS = "sensors"     # list sensor streams the world publishes
OP_SENSOR = "sensor"       # one frame of one stream
OP_RAYS = "rays"           # horizontal range rays from the body origin
OP_EPOCH = "epoch"
OP_CLOSE = "close"

PD_IMPLICIT = "implicit"          # kd into dof_damping, torque = kp*(q*-q)
PD_EXPLICIT = "explicit"          # torque = kp*(q*-q) - kd*qd, model damping zeroed
PD_POSITION_ACTUATOR = "position_actuator"   # targets go to the MJCF position actuators


@dataclass
class ActuatorSpec:
    """How the world's 'motor firmware' should drive this body's joints."""
    joint_names: list[str]
    pd_mode: str = PD_IMPLICIT
    kp: list[float] = field(default_factory=list)
    kd: list[float] = field(default_factory=list)
    torque_limit: list[float] = field(default_factory=list)
    default_pos: list[float] = field(default_factory=list)


@dataclass
class BusState:
    t: float
    joint_pos: list[float]
    joint_vel: list[float]
    imu_quat: list[float] = field(default_factory=list)      # w x y z
    imu_gyro: list[float] = field(default_factory=list)
    imu_acc: list[float] = field(default_factory=list)
    odom_xy: list[float] = field(default_factory=list)       # onboard estimate (sim: from physics)
    odom_yaw: float = 0.0
    base_height: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BusCommand:
    targets: list[float]
    kp: list[float] | None = None
    kd: list[float] | None = None


class MotorBus(Protocol):
    """The body node's view of the world or the hardware. One instance per body."""

    def spawn(self, spec: ActuatorSpec) -> dict: ...

    def read(self) -> BusState: ...

    def write(self, cmd: BusCommand) -> None: ...

    def reset(self) -> None: ...

    def sensors(self) -> list[str]: ...

    def sensor(self, name: str) -> tuple[bytes, str]: ...      # (data, mime)

    def rays(self, angles_deg: list[float], max_range_m: float) -> list[float]: ...

    def epoch(self) -> str: ...

    def close(self) -> None: ...


# ---- control plane HTTP (world node) ----------------------------------------------------
HEALTH = "/health"
STATUS = "/status"
RESET = "/reset"
STREAM = "/stream"
SENSORS = "/sensors"           # GET list; GET /sensors/<name> one frame (for the platform)
SPAWN = "/spawn"
