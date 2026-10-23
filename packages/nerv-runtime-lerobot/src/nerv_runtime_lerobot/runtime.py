"""One LeRobot owner with shared camera sources and revocable native action dispatch."""
from __future__ import annotations

from contextlib import nullcontext

import io
import hashlib
import json
import threading
import time
from collections import deque
import math
import uuid
import os

from nerv_runtime_sdk.policies import backend
from .device import DeviceSession
from .standard import CHANNELS, validate_manifest, validate_action
from pathlib import Path

from nerv_runtime_sdk.contracts import PolicyArtifact
from nerv_runtime_sdk.control import DispatchAuthority, ManagedExecutions


class LeRobotRuntime:
    guarded_dispatch = True
    def __init__(self, profile, binding):
        self.profile, self.binding = profile, binding
        self.name, self.family, self.version = profile.id, profile.family, "0.8.0"
        self.settings = binding.configuration
        required = ("control_rate_hz", "max_runtime_s", "supervision_timeout_s", "cancel_timeout_s",
                    "watchdog_poll_s", "max_frame_age_s", "max_action_age_s", "policy_device", "jpeg_quality", "use_degrees")
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
        self._device = DeviceSession(self.settings)
        self._io = self._device.io
        self._runner = None
        self._policy_id = ""
        self._hold_writes = 0
        self._observations = deque(maxlen=64)  # Bounded receipts, not an image cache.
        self._sequence = 0
        self._instance = uuid.uuid4().hex
        self._decision_positions = {}
        self._last_action = None
        self._action_log = Path(os.environ.get("NERV_HOME", str(Path.home() / ".nerv"))) / "runtime-events" / f"{self._instance}.jsonl"
        self._artifacts = {}
        self.execution.on_stop = self._hold_measured
        for name, spec in profile.configuration.get("skills", {}).items():
            if spec.get("manifest"):
                artifact = PolicyArtifact(**json.loads(Path(spec["manifest"]).read_text(encoding="utf-8")))
                validate_manifest(artifact.model_dump())
                backend(artifact.format)
                self._artifacts[name] = artifact.model_dump()

    @property
    def held(self):
        return self.dispatch.held

    @property
    def _robot(self):
        return self._device.robot

    @_robot.setter
    def _robot(self, robot):
        self._device.robot = robot

    def prepare(self):
        with self._lifecycle:
            if self.held:
                raise RuntimeError("release the emergency stop before connecting")
            from . import LeRobotProvider
            source = LeRobotProvider().describe().source
            expected = self.settings.get("sdk_source_sha256")
            if expected and expected != source["sdk_source_sha256"]:
                raise RuntimeError("native SDK source differs from the frozen binding")
            self._preparation = {**self._device.prepare(), "source": source}
        return {"ok": True, "prepared": True, "preparation": self._preparation}

    def _connect_calibrated(self, robot):
        return self._device.connect_calibrated(robot)

    def _positions(self):
        return self._device.positions()

    def _capture(self):
        return self._device.capture()

    def _camera_frame(self, camera):
        return self._device.camera_frame(camera)

    def observe(self):
        if self._robot is None:
            return {"prepared": False}, []
        from PIL import Image
        raw, stamps = self._capture()
        captured_monotonic = time.monotonic() - max(time.perf_counter() - stamp for stamp in stamps.values())
        images = []
        for name in self._robot.cameras:
            target = io.BytesIO()
            Image.fromarray(raw[name]).save(target, format="PNG")
            images.append((f"camera:{name}", target.getvalue()))
        with self._frames:
            self._sequence += 1
            sequence = self._sequence
            self._observations.append((sequence, captured_monotonic))
            self._decision_positions[sequence] = {k: v for k, v in raw.items() if k.endswith(".pos")}
            valid = {seq for seq, _ in self._observations}
            self._decision_positions = {seq: v for seq, v in self._decision_positions.items() if seq in valid}
        state = {"prepared": True, "joint_positions": {k: v for k, v in raw.items() if k.endswith(".pos")},
                 "frame_seq": sequence, "instance_id": self._instance, "observed_at": time.time(),
                 "camera_capture_clock": "perf_counter", "camera_captured_at_s": stamps,
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
        with self._lifecycle:
            if self.execution.status()["running"]:
                raise RuntimeError("cannot change policy during execution")
            if policy_id not in self._artifacts:
                raise ValueError("policy id is not registered for this robot")
            if self._runner is not None and hasattr(self._runner, "close"):
                self._runner.close()
            self._runner = None
            self._policy_id = ""
            artifact = self._artifacts[policy_id]
            self._runner = backend(artifact["format"])(artifact, self.settings["policy_device"])
            self._policy_id = policy_id
        return {"ok": True, "policy": policy_id}

    def set_armed(self, enabled):
        if enabled and self._robot is None:
            raise RuntimeError("prepare the device before enabling control")
        self.dispatch.enable(enabled)

    def actions(self):
        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        result = self._primitive_actions() + [{"name": name, "description": spec["description"], "parameters": empty, "kind": "skill"}
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
        if name in ("read_state", "move_joints", "nudge_joints", "set_gripper"):
            return self._primitive(name, args, _control_guard)
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
        from .policy import artifact_files
        artifact_files(self._artifacts[name])
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
                        if any(time.perf_counter() - stamp > float(self.settings["max_action_age_s"]) for stamp in _stamps.values()):
                            raise TimeoutError("inference outlived its camera frame freshness limit")
                        with self._io:
                            self._write(token, action)
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
                if any(not math.isfinite(v) for v in positions.values()):
                    raise ValueError("invalid measured position; hold cannot be acknowledged")
                self._send_recorded(positions, kind="hold")
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
        with self._frames:
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
                "last_action": self._last_action, "instance_id": self._instance,
                "preparation": getattr(self, "_preparation", {}), "prepare_fault": getattr(self, "_prepare_fault", None)}

    def config_options(self):
        return []

    def set_option(self, key, value):
        return {"ok": False, "message": "runtime settings belong to the registered binding"}

    def close(self):
        with self._lifecycle:
            self.dispatch.enable(False)
            self.cancel_current("session_closed")
            self._device.close()
            if self._runner is not None and not self.execution.status()["running"]:
                if hasattr(self._runner, "close"):
                    self._runner.close()
                self._runner = None
                self._policy_id = ""

    def torque_off(self):
        with self._lifecycle:
            self.dispatch.enable(False)
            self.cancel_current("operator_torque_off")
            self._device.close(torque_off=True)
        return {"ok": True, "prepared": False, "message": "Device disconnected with torque disabled"}

    def dry_run(self):
        with self._lifecycle:
            if self.execution.status()["running"] or self.dispatch.enabled:
                raise RuntimeError("disarm and stop execution before a no-motion check")
            if self._runner is None:
                raise ValueError("load a policy first")
            raw, stamps = self._capture()
            self._runner.reset()
            try:
                action = self._runner.action(raw)
                validate_action(action)
                return {"ok": True, "action": action, "device_write": False,
                        "camera_age_s": {k: time.perf_counter() - v for k, v in stamps.items()}}
            finally:
                self._runner.reset()

    def _send_recorded(self, action, kind="motion"):
        receipt = {"requested": dict(action), "sent": None, "measured": None, "kind": kind,
                   "time": time.time(), "instance_id": self._instance}
        # Persist intent before device I/O; a process crash cannot look like no command.
        self._action_log.parent.mkdir(parents=True, exist_ok=True)
        def record(value):
            with self._action_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(value, allow_nan=False) + "\n")
        record({**receipt, "phase": "requested"})
        try:
            receipt["sent"] = self._robot.send_action(action)
            receipt["adjusted"] = receipt["sent"] != action
            receipt["measured"] = self._positions()
            return receipt
        except Exception as error:
            receipt["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            self._last_action = receipt
            record({**receipt, "phase": "result"})

    def _write(self, token, action):
        def write():
            self._device.validate_targets(action)
            return self._send_recorded(action)
        return self.dispatch.write(token, write)

    def _primitive_actions(self):
        from .action_catalog import primitive_actions
        return primitive_actions(self.settings)

    def _primitive(self, name, args, guard):
        if name == "read_state":
            state, _ = self.observe()
            return {"ok": True, "message": "Current observation", "data": state}
        import jsonschema
        schema = next(a["parameters"] for a in self._primitive_actions() if a["name"] == name)
        jsonschema.validate(args, schema)
        duration = args["duration_s"]
        if not math.isfinite(duration):
            raise ValueError("duration must be finite")
        with self._lifecycle:
            if self.execution.status()["running"]:
                raise RuntimeError("stop the current execution before changing controllers")
            with self._frames:
                receipt = next((stamp for seq, stamp in self._observations if seq == args["frame_seq"]), None)
                observed = self._decision_positions.get(args["frame_seq"])
            if (args["instance_id"] != self._instance or receipt is None
                    or observed is None
                    or time.monotonic() - receipt > self.settings["decision_max_age_s"]):
                raise ValueError("decision observation expired; observe and decide again")
            token = self.dispatch.token()
            start = self._positions()
            if any(abs(start[k] - observed[k]) > self.settings["decision_max_drift"] for k in CHANNELS):
                raise ValueError("device moved since the decision; observe and decide again")
            goal = dict(start)
            if name == "set_gripper":
                goal["gripper.pos"] = args["percent"]
            else:
                for joint, value in args["targets"].items():
                    key = f"{joint}.pos"
                    goal[key] = start[key] + value if name == "nudge_joints" else value
                    if abs(goal[key] - start[key]) > self.settings["max_step_deg"]:
                        raise ValueError("joint motion exceeds the standard single-command limit")
            self._device.validate_targets(goal)
            def operation(owned, stop):
                begun = time.monotonic()
                while not stop.is_set():
                    self._capture()  # camera loss also stops direct control
                    fraction = min(1.0, (time.monotonic() - begun) / duration)
                    action = {k: start[k] + (goal[k] - start[k]) * fraction for k in CHANNELS}
                    with self._io:
                        result = self._write(owned, action)
                    if fraction >= 1:
                        return {"reason": "trajectory_ended", "task_success": None, **result,
                                "target_reached": all(abs(result["measured"][k] - goal[k]) <= self.settings["settle_tol"] for k in CHANNELS)}
                    stop.wait(1 / self.settings["control_rate_hz"])
                return {"reason": "stopped", "task_success": None}
            with guard():
                state = self.execution.start(name, operation, expected_token=token)
            return {"ok": True, "message": "Motion started; observe its result", "data": state}
