"""The sole owner of a native LeRobot device and its camera threads."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from importlib.metadata import version

from filelock import FileLock, Timeout

from .standard import LEROBOT_VERSION, WIDTH, HEIGHT, validate_action, validate_cameras


class DeviceSession:
    def __init__(self, settings):
        self.settings = settings
        self.robot = None
        self.io = threading.RLock()
        self.lock = None
        self.fault = None
        self.preparation = {}

    def prepare(self):
        if self.fault:
            raise RuntimeError("device cleanup failed; operator inspection required")
        if self.robot is not None:
            return self.preparation
        if version("lerobot") != LEROBOT_VERSION:
            raise RuntimeError(f"adapter requires LeRobot {LEROBOT_VERSION}")
        settings = self.settings
        if not all(settings.get(k) for k in ("port", "id", "calibration_dir", "camera_config_file")):
            raise ValueError("configure port, robot ID, calibration directory and camera file")
        if not settings.get("use_degrees"):
            raise ValueError("standard SO-101 requires use_degrees=true")
        cameras = json.loads(Path(settings["camera_config_file"]).read_text(encoding="utf-8"))
        validate_cameras(cameras)
        from lerobot.robots.so_follower.config_so_follower import SOFollowerRobotConfig
        from lerobot.robots import make_robot_from_config
        from lerobot.cameras.opencv import OpenCVCameraConfig
        calibration_dir = Path(settings["calibration_dir"]).expanduser().resolve(strict=True)
        camera_configs = {name: OpenCVCameraConfig(**{k: v for k, v in c.items() if k != "type"})
                          for name, c in cameras.items()}
        relative = settings.get("max_relative_target") or None
        if isinstance(relative, str):
            relative = float(relative)
        config = SOFollowerRobotConfig(port=settings["port"], id=settings["id"],
            calibration_dir=calibration_dir, use_degrees=True, cameras=camera_configs,
            disable_torque_on_disconnect=False, max_relative_target=relative)
        robot = make_robot_from_config(config)
        if not robot.calibration:
            raise ValueError("existing LeRobot calibration is required; calibrate outside NERV")
        device_key = os.path.normcase(os.path.realpath(settings["port"]))
        root = Path(os.environ.get("NERV_HOME", str(Path.home() / ".nerv"))) / "device-locks"
        root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(root / (hashlib.sha256(device_key.encode()).hexdigest() + ".lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout as error:
            raise RuntimeError("another NERV runtime owns this device; close it first") from error
        self.lock = lock
        try:
            self.connect_calibrated(robot)
        except BaseException:
            if not self.fault:
                lock.release()
                self.lock = None
            else:
                self.robot = robot
            raise
        self.robot = robot
        self.preparation = {"prepared_at": time.time(),
            "calibration_sha256": hashlib.sha256(json.dumps(robot.calibration, sort_keys=True, default=str).encode()).hexdigest(),
            "camera_configuration_sha256": hashlib.sha256(json.dumps(cameras, sort_keys=True).encode()).hexdigest()}
        return self.preparation

    def connect_calibrated(self, robot):
        if not robot.calibration:
            raise ValueError("native calibration is required before connecting this device")
        try:
            robot.connect(calibrate=False)
            if not robot.is_calibrated:
                raise ValueError("motor calibration differs from the selected file; use LeRobot calibration outside NERV")
        except BaseException:
            errors = []
            for component in [*robot.cameras.values(), robot.bus]:
                try:
                    if component.is_connected:
                        component.disconnect()
                except Exception as error:
                    errors.append(type(error).__name__)
            if errors:
                self.fault = ",".join(errors)
            raise

    def positions(self):
        if self.robot is None:
            raise RuntimeError("connect the device first")
        with self.io:
            values = self.robot.bus.sync_read("Present_Position", num_retry=self.robot.config.num_read_retries)
        return {f"{k}.pos": float(v) for k, v in values.items()}

    def camera_frame(self, camera):
        # LeRobot 0.6.1 read_latest() omits timestamps. Read the same protected
        # frame/timestamp pair here so model latency cannot refresh stale pixels.
        with camera.frame_lock:
            frame, stamp = camera.latest_frame, camera.latest_timestamp
        if frame is None or stamp is None or time.perf_counter() - stamp > self.settings["max_frame_age_s"]:
            raise TimeoutError("camera frame unavailable or stale")
        return frame, stamp

    def capture(self):
        if self.robot is None:
            raise RuntimeError("connect the device first")
        # Native get_observation owns normal observation semantics. Replace image
        # values with timestamped snapshots from those SAME camera objects.
        with self.io:
            raw = self.robot.get_observation()
        stamps = {}
        for name, camera in self.robot.cameras.items():
            raw[name], stamps[name] = self.camera_frame(camera)
            if raw[name].shape != (HEIGHT, WIDTH, 3):
                raise ValueError(f"{name}: captured image does not match the standard 640 x 480 RGB contract")
        validate_action({key: value for key, value in raw.items() if key.endswith(".pos")})
        if any(time.perf_counter() - stamp > self.settings["max_frame_age_s"] for stamp in stamps.values()):
            raise TimeoutError("camera frame became stale during capture")
        return raw, stamps

    def validate_targets(self, action):
        validate_action(action)
        # Convert the actual calibration's encoder limits through LeRobot's own
        # normalization. Never invent degree ranges for a particular workbench.
        bus = self.robot.bus
        calibration = self.robot.calibration
        for key, target in action.items():
            name = key.removesuffix(".pos")
            if name == "gripper":
                continue
            c = calibration[name]
            bounds = [bus._normalize({bus.motors[name].id: raw})[bus.motors[name].id]
                      for raw in (c.range_min, c.range_max)]
            if not min(bounds) <= target <= max(bounds):
                raise ValueError(f"{name}: target outside calibrated range")

    def close(self, *, torque_off=False):
        with self.io:
            if self.robot is not None:
                self.robot.config.disable_torque_on_disconnect = torque_off
                self.robot.disconnect()
                self.robot = None
            if self.lock:
                self.lock.release()
                self.lock = None
