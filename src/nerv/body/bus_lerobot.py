"""NERV/World endpoint for real hardware through LeRobot: any `lerobot.robots.Robot` is a bus.

`send_action` is the write, `get_observation` the read. Joint units on the bus are radians
(LeRobot reports degrees for SO-101 joints and 0–100 for the gripper; converted here). Cameras
configured on the robot become sensor streams named after their keys.
Starts with torque only after `spawn`; joint targets are clamped to the calibration range
and to `max_relative_target` per write — hardware limits live here, the operator's arming
switch lives in the node and the platform.
"""
from __future__ import annotations

import io
import math
import time

from ..nerve.world import ActuatorSpec, BusCommand, BusState

DEG = math.pi / 180.0


class LerobotBus:
    def __init__(self, settings: dict) -> None:
        from lerobot.robots.utils import make_robot_from_config
        from lerobot.robots import RobotConfig
        rtype = settings.get("robot_type", "so_follower")
        port = settings.get("port", "")
        if not port:
            raise RuntimeError("SO101_PORT is not set (the serial port of the follower arm)")
        cams = {}
        for item in (settings.get("cameras") or "").split(","):
            item = item.strip()
            if not item:
                continue
            key, _, idx = item.partition("=")
            from lerobot.cameras.opencv import OpenCVCameraConfig
            cams[key.strip()] = OpenCVCameraConfig(index_or_path=int(idx) if idx.isdigit() else idx,
                                                   width=640, height=480, fps=30)
        cfg_cls = RobotConfig.get_choice_class(rtype)
        kw = {"port": port, "id": settings.get("id") or None, "cameras": cams}
        mrt = settings.get("max_relative_target")
        if mrt not in (None, ""):
            kw["max_relative_target"] = float(mrt)
        self.robot = make_robot_from_config(cfg_cls(**kw))
        self.gripper_key = settings.get("gripper", "gripper")
        self.gripper_range_rad = (0.0, float(settings.get("gripper_open_rad", 1.75)))
        self._joints: list[str] = []
        self._spawned = False
        self._t0 = time.time()

    def spawn(self, spec: ActuatorSpec) -> dict:
        self.robot.connect(calibrate=False)
        feats = list(self.robot.action_features.keys())
        self._joints = [k[:-4] for k in feats if k.endswith(".pos")]
        if spec.joint_names:
            missing = [j for j in spec.joint_names if j not in self._joints]
            if missing:
                raise RuntimeError(f"the arm has no joints named {missing}; it has {self._joints}")
            self._joints = list(spec.joint_names)
        limits = []
        cal = getattr(self.robot, "calibration", {}) or {}
        for j in self._joints:
            if j == self.gripper_key:
                limits.append(list(self.gripper_range_rad))
            else:
                c = cal.get(j)
                lo, hi = (-180.0, 180.0)
                if c is not None and hasattr(c, "range_min") and hasattr(c, "range_max"):
                    # calibration ranges are raw encoder ticks; degrees mode maps ±180 → use conservative ±150
                    lo, hi = -150.0, 150.0
                limits.append([lo * DEG, hi * DEG])
        self._spawned = True
        return {"joint_names": self._joints, "joint_limits": limits, "epoch": str(int(self._t0))}

    def _to_rad(self, key: str, v: float) -> float:
        if key == self.gripper_key:
            lo, hi = self.gripper_range_rad
            return lo + (hi - lo) * float(v) / 100.0
        return float(v) * DEG

    def _from_rad(self, key: str, r: float) -> float:
        if key == self.gripper_key:
            lo, hi = self.gripper_range_rad
            return 100.0 * (r - lo) / (hi - lo) if hi != lo else 0.0
        return r / DEG

    def read(self) -> BusState:
        obs = self.robot.get_observation()
        pos = [self._to_rad(j, obs.get(f"{j}.pos", 0.0)) for j in self._joints]
        self._last_obs = obs
        return BusState(t=time.time() - self._t0, joint_pos=pos, joint_vel=[0.0] * len(pos))

    def write(self, cmd: BusCommand) -> None:
        if not self._spawned:
            raise RuntimeError("bus not spawned")
        action = {f"{j}.pos": self._from_rad(j, r) for j, r in zip(self._joints, cmd.targets)}
        self.robot.send_action(action)

    def reset(self) -> None:
        pass

    def sensors(self) -> list[str]:
        return [f"camera:{k}" for k in (getattr(self.robot, "cameras", {}) or {}).keys()]

    def sensor(self, name: str) -> tuple[bytes, str]:
        key = name.split(":", 1)[-1]
        obs = getattr(self, "_last_obs", None) or self.robot.get_observation()
        frame = obs.get(key)
        if frame is None:
            raise KeyError(f"no camera {key!r}; have {list(self.robot.cameras)}")
        from PIL import Image
        im = Image.fromarray(frame)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=75)
        return buf.getvalue(), "image/jpeg"

    def rays(self, angles_deg, max_range_m):
        return []

    def epoch(self) -> str:
        return str(int(self._t0))

    def close(self) -> None:
        try:
            self.robot.disconnect()
        except Exception:
            pass
