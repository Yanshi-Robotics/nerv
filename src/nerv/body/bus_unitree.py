"""NERV/World endpoint for a real Unitree G1 through LeRobot's `unitree_g1` ZMQ bridge.

⚠️ UNVERIFIED. Written against the shape of `lerobot.robots.unitree_g1` (a ZMQ socket to a
G1 server publishing LowState and accepting LowCmd) without hardware to run it on. It is
registered with `verified: false`; the platform refuses to launch an unverified endpoint
unless NERV_ALLOW_UNVERIFIED=1, and even then the session starts disarmed.
"""
from __future__ import annotations

import time

from ..nerve.world import ActuatorSpec, BusCommand, BusState


class UnitreeBus:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.robot = None
        self._t0 = time.time()
        self._joints: list[str] = []

    def spawn(self, spec: ActuatorSpec) -> dict:
        from lerobot.robots.unitree_g1.config_unitree_g1 import UnitreeG1Config
        from lerobot.robots.unitree_g1.unitree_g1 import UnitreeG1
        cfg = UnitreeG1Config(robot_ip=self.settings.get("robot_ip", ""), is_simulation=False)
        self.robot = UnitreeG1(cfg)
        self.robot.connect(calibrate=False)
        self._joints = list(spec.joint_names)
        return {"joint_names": self._joints, "joint_limits": [], "epoch": self.epoch(),
                "unverified": True}

    def read(self) -> BusState:
        obs = self.robot.get_observation()
        pos = [float(obs.get(f"{j}.pos", 0.0)) for j in self._joints]
        vel = [float(obs.get(f"{j}.vel", 0.0)) for j in self._joints]
        return BusState(t=time.time() - self._t0, joint_pos=pos, joint_vel=vel,
                        imu_quat=list(obs.get("imu.quat", []) or []),
                        imu_gyro=list(obs.get("imu.gyro", []) or []))

    def write(self, cmd: BusCommand) -> None:
        action = {f"{j}.pos": float(r) for j, r in zip(self._joints, cmd.targets)}
        self.robot.send_action(action)

    def reset(self) -> None:
        pass

    def sensors(self) -> list[str]:
        return []

    def sensor(self, name: str):
        raise KeyError("no sensor streams on the unverified G1 endpoint")

    def rays(self, angles_deg, max_range_m):
        return []

    def clearance(self, query: dict) -> dict:
        return {"valid": False, "reason": "unavailable", "message": "no verified clearance sensor"}

    def epoch(self) -> str:
        return str(int(self._t0))

    def close(self) -> None:
        if self.robot is not None:
            try:
                self.robot.disconnect()
            except Exception:
                pass
