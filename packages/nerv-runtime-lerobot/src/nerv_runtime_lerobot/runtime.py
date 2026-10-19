"""One LeRobot owner with shared camera sources and revocable native action dispatch."""
from __future__ import annotations

from contextlib import nullcontext

import io
import hashlib
import json
import threading
import time
from collections import deque
from importlib.metadata import version
from pathlib import Path

from nerv_runtime_sdk.contracts import PolicyArtifact
from nerv_runtime_sdk.control import DispatchAuthority, ManagedExecutions


class LeRobotRuntime:
    guarded_dispatch = True
    def __init__(self, profile, binding):
        self.profile, self.binding = profile, binding
        self.name, self.family, self.version = profile.id, profile.family, "0.6.0"
        self.settings = binding.configuration
        required = ("control_rate_hz", "max_runtime_s", "supervision_timeout_s", "cancel_timeout_s",
                    "watchdog_poll_s", "max_frame_age_s", "policy_device", "jpeg_quality", "use_degrees")
        for key in required:
            if key not in self.settings:
                raise ValueError(f"runtime configuration requires {key}")
        self.dispatch = DispatchAuthority()
        self.execution = ManagedExecutions(
            self.dispatch, supervision_timeout_s=float(self.settings["supervision_timeout_s"]),
            cancel_timeout_s=float(self.settings["cancel_timeout_s"]),
            poll_s=float(self.settings["watchdog_poll_s"]))
        self._io = threading.RLock()
        self._lifecycle = threading.RLock()
        self._frames = threading.Lock()
        self._robot = None
        self._runner = None
        self._policy_id = ""
        self._hold_writes = 0
        self._observations = deque(maxlen=64)  # Bounded receipts, not an image cache.
        self._sequence = 0
        self._artifacts = {}
        self.execution.on_stop = self._hold_measured
        for name, spec in profile.configuration.get("skills", {}).items():
            if spec.get("manifest"):
                artifact = PolicyArtifact(**json.loads(Path(spec["manifest"]).read_text()))
                if artifact.format != "lerobot-act" or getattr(artifact, "sdk_version", None) != "0.6.1":
                    raise ValueError("unsupported policy artifact format or SDK contract")
                self._artifacts[name] = artifact.model_dump()

    @property
    def held(self):
        return self.dispatch.held

    def prepare(self):
        with self._lifecycle:
            if getattr(self, "_prepare_fault", None):
                raise RuntimeError("partial device cleanup failed; operator inspection is required")
            if self._robot is not None:
                return {"ok": True, "prepared": True}
            if self.held:
                raise RuntimeError("release the emergency stop before preparing equipment")
            from . import LeRobotProvider
            source = LeRobotProvider().describe().source
            expected = self.settings.get("sdk_source_sha256")
            if expected and expected != source["sdk_source_sha256"]:
                raise RuntimeError("native SDK source fingerprint differs from the frozen binding")
            if version("lerobot") != "0.6.1":
                raise RuntimeError("this adapter requires the audited LeRobot 0.6.1")
            from lerobot.robots.so_follower.config_so_follower import SOFollowerRobotConfig
            from lerobot.robots import make_robot_from_config
            from lerobot.cameras.opencv import OpenCVCameraConfig
            camera_file = self.settings.get("camera_config_file", "")
            if not camera_file or not self.settings.get("port"):
                raise ValueError("configure the serial port and camera configuration file")
            declared = json.loads(Path(camera_file).read_text())
            for artifact in self._artifacts.values():
                for name in artifact["image_names"]:
                    expected = artifact["input_features"][f"observation.images.{name}"]["shape"]
                    camera = declared.get(name, {})
                    if [camera.get("height"), camera.get("width")] != expected[1:]:
                        raise ValueError("camera configuration does not match the frozen policy input contract")
            cameras = {key: OpenCVCameraConfig(**{k: v for k, v in value.items() if k != "type"})
                       for key, value in declared.items()}
            relative_target = self.settings.get("max_relative_target") or None
            if isinstance(relative_target, str):
                relative_target = float(relative_target)
            config = SOFollowerRobotConfig(port=self.settings["port"], id=self.settings.get("id"),
                                           cameras=cameras, use_degrees=bool(self.settings["use_degrees"]),
                                           disable_torque_on_disconnect=self.settings["disable_torque_on_disconnect"],
                                           max_relative_target=relative_target)
            robot = make_robot_from_config(config)
            # Explicit operator preparation: native connect configures motors and
            # enables torque. No caller outside this lifecycle owns this robot.
            self._connect_calibrated(robot)
            self._robot = robot
            self._preparation = {"prepared_at": time.time(), "source": source,
                "calibration_sha256": hashlib.sha256(json.dumps(robot.calibration, sort_keys=True, default=str).encode()).hexdigest(),
                "camera_configuration_sha256": hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest()}
        return {"ok": True, "prepared": True, "sdk_version": version("lerobot")}

    def _connect_calibrated(self, robot):
        if not robot.calibration:
            raise ValueError("native calibration is required before connecting this device")
        try:
            robot.connect(calibrate=False)
        except Exception:
            cleanup_errors = []
            for component in [*robot.cameras.values(), robot.bus]:
                try:
                    if component.is_connected:
                        component.disconnect()
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
            if cleanup_errors:
                self._prepare_fault = ",".join(cleanup_errors)
            raise

    def _positions(self):
        if self._robot is None:
            raise RuntimeError("prepare the device first")
        with self._io:
            values = self._robot.bus.sync_read("Present_Position", num_retry=self._robot.config.num_read_retries)
        return {f"{key}.pos": float(value) for key, value in values.items()}

    def _capture(self):
        if self._robot is None:
            raise RuntimeError("prepare the device first")
        raw, stamps = {}, {}
        for key, camera in self._robot.cameras.items():
            raw[key], stamps[key] = self._camera_frame(camera)
        raw.update(self._positions())
        if any(time.perf_counter() - stamp > float(self.settings["max_frame_age_s"]) for stamp in stamps.values()):
            raise TimeoutError("camera frames became stale while reading device state")
        return raw, stamps

    def _camera_frame(self, camera):
        with camera.frame_lock:
            frame, stamp = camera.latest_frame, camera.latest_timestamp
        if frame is None or stamp is None or time.perf_counter() - stamp > float(self.settings["max_frame_age_s"]):
            raise TimeoutError("camera frame unavailable or stale")
        return frame, stamp

    def observe(self):
        if self._robot is None:
            return {"prepared": False}, []
        from PIL import Image
        raw, stamps = self._capture()
        images = []
        for name in self._robot.cameras:
            target = io.BytesIO()
            Image.fromarray(raw[name]).save(target, format="PNG")
            images.append((f"camera:{name}", target.getvalue()))
        with self._frames:
            self._sequence += 1
            sequence = self._sequence
            self._observations.append((sequence, time.monotonic()))
        state = {"prepared": True, "joint_positions": {k: v for k, v in raw.items() if k.endswith(".pos")},
                 "frame_seq": sequence, "observed_at": time.time(),
                 "camera_age_s": {k: time.perf_counter() - v for k, v in stamps.items()},
                 "execution": self.execution.status(), "loaded_policy": self._policy_id}
        return state, images

    def sensor_names(self):
        return [f"camera:{name}" for name in self._robot.cameras] if self._robot else []

    def sensor_jpeg(self, sensor_id):
        if self._robot is None or sensor_id not in self.sensor_names():
            return None
        from PIL import Image
        key = sensor_id.split(":", 1)[1]
        frame, _stamp = self._camera_frame(self._robot.cameras[key])
        target = io.BytesIO()
        Image.fromarray(frame).save(target, format="JPEG", quality=int(self.settings["jpeg_quality"]))
        return target.getvalue()

    def supervision_sensors(self):
        return self.sensor_names()

    def stream_jpeg(self):
        sensors = self.sensor_names()
        return self.sensor_jpeg(sensors[0]) if sensors else None

    def policies(self):
        return [{"id": key, "format": value["format"], "conditions": value["conditions"],
                 "loaded": key == self._policy_id,
                 "artifact_signature": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}
                for key, value in self._artifacts.items()]

    def load_policy(self, policy_id):
        from .policy import ACTRunner
        with self._lifecycle:
            if self.execution.status()["running"]:
                raise RuntimeError("cannot change policy during execution")
            if policy_id not in self._artifacts:
                raise ValueError("policy id is not registered for this robot")
            self._runner = ACTRunner(self._artifacts[policy_id], self.settings["policy_device"])
            self._policy_id = policy_id
        return {"ok": True, "policy": policy_id}

    def set_armed(self, enabled):
        if enabled and self._robot is None:
            raise RuntimeError("prepare the device before enabling control")
        self.dispatch.enable(enabled)

    def tools(self):
        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        result = [{"name": name, "description": spec["description"], "parameters": empty, "kind": "skill"}
                  for name, spec in self.profile.configuration.get("skills", {}).items()]
        for name, description, properties, required, kind in [
            ("execution_status", "Read the current execution state; stopping is not proof of task success.", {}, [], "read"),
            ("wait_execution", "Wait briefly, then observe again to supervise the running skill.",
             {"execution_id": {"type": "string"}, "seconds": {"type": "number", "minimum": 0,
              "maximum": float(self.settings["supervision_timeout_s"]) / 2}}, ["execution_id", "seconds"], "read"),
            ("cancel_execution", "End this execution. Does not return to an initial pose or claim task success.",
             {"execution_id": {"type": "string"}}, ["execution_id"], "control")]:
            result.append({"name": name, "description": description, "kind": kind,
                           "parameters": {"type": "object", "properties": properties, "required": required,
                                          "additionalProperties": False}})
        return result

    def invoke(self, name, *, _progress=None, _control_guard=nullcontext, **args):
        if name == "execution_status":
            return {"ok": True, "message": "Execution state", "data": self.execution.status()}
        if name == "wait_execution":
            value = self.execution.wait(args["execution_id"], float(args["seconds"]))
            return {"ok": True, "message": "Execution observation due", "data": value}
        if name == "cancel_execution":
            with _control_guard():
                if args["execution_id"] != self.execution.status().get("execution_id"):
                    raise ValueError("unknown execution")
                return self.cancel_current("agent_cancelled")
        if name not in self._artifacts:
            raise ValueError("unknown skill")
        with self._lifecycle:
            expected_token = self.dispatch.token()
            if self.execution.status()["running"]:
                raise RuntimeError("another execution is still running")
            if self._runner is None or self._policy_id != name:
                self.load_policy(name)
            runner = self._runner
            runner.reset()

            def operation(token, stop):
                started = time.monotonic()
                try:
                    while not stop.is_set():
                        tick = time.monotonic()
                        if tick - started >= float(self.settings["max_runtime_s"]):
                            return {"reason": "time_limit", "task_success": None}
                        raw, _stamps = self._capture()
                        action = runner.action(raw)
                        if any(time.perf_counter() - stamp > float(self.settings["max_frame_age_s"]) for stamp in _stamps.values()):
                            raise TimeoutError("inference outlived its camera frame freshness limit")
                        with self._io:
                            self.dispatch.write(token, lambda: self._robot.send_action(action))
                        stop.wait(max(0, 1 / float(self.settings["control_rate_hz"]) - (time.monotonic() - tick)))
                    return {"reason": "stopped", "task_success": None}
                finally:
                    runner.reset()

            with _control_guard():
                state = self.execution.start(name, operation, expected_token=expected_token)
            return {"ok": True, "message": "Skill running; observe and supervise until completion or cancellation.",
                    "data": state}

    def _hold_measured(self):
        if self._robot is not None:
            with self._io:
                positions = self._positions()
                self._robot.send_action(positions)
                self._hold_writes += 1

    def cancel_current(self, reason):
        state = self.execution.cancel(reason=reason)
        return {"ok": True, "message": "Dispatch stopped; device session retained", "data": state}

    def hold(self, reason="operator"):
        self.dispatch.revoke(hold=True)
        self.cancel_current(reason)
        return {"ok": True, "held": True, "message": "Emergency stop latched; not a power cut"}

    def release(self):
        self.dispatch.release()
        return {"ok": True, "held": False, "message": "Hold released; enable control before a new task"}

    def supervise(self, frame_seq):
        receipt = next((stamp for seq, stamp in self._observations if seq == frame_seq), None)
        if receipt is None or time.monotonic() - receipt > float(self.settings["supervision_timeout_s"]):
            return {"ok": False, "message": "supervision requires a recent observation receipt"}
        self.execution.supervise()
        return {"ok": True}

    def execution_status(self):
        return self.execution.status()

    def status(self):
        return {"prepared": self._robot is not None, "held": self.held,
                "hold_writes": self._hold_writes, "loaded_policy": self._policy_id, "policies": self.policies(), "execution": self.execution.status(),
                "preparation": getattr(self, "_preparation", {}), "prepare_fault": getattr(self, "_prepare_fault", None)}

    def config_options(self):
        return []

    def set_option(self, key, value):
        return {"ok": False, "message": "runtime settings belong to the registered binding"}

    def close(self):
        self.cancel_current("session_closed")
        if self._robot is not None:
            self._robot.disconnect()
            self._robot = None
