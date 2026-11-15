"""Adapter for existing simulated families; construction has no simulation side effects."""
from __future__ import annotations

from contextlib import nullcontext
import threading
import math
import time
from collections import OrderedDict

from nerv_runtime_sdk.contracts import RuntimeManifest


class BuiltinProvider:
    def describe(self):
        return RuntimeManifest(id="builtin", version="0.7.1", features=["sim", "named_sensors"])

    def create(self, profile, binding):
        if binding.mode != "sim":
            raise ValueError("the builtin adapter only handles simulation")
        return BuiltinRuntime(profile, binding)


class BuiltinRuntime:
    guarded_dispatch = True
    def __init__(self, profile, binding):
        self.profile, self.binding = profile, binding
        self.name, self.family, self.version = profile.id, profile.family, "0.7.1"
        self._impl = None
        self._last_skill = None
        self._last_skill_stopped = True
        self._sensor_bus = None
        self._sensor_lock = threading.Lock()
        self._supervision_lock = threading.RLock()
        self._frames = OrderedDict()
        self._frame_seq = 0
        self._last_ack = 0
        self._supervision_deadline = None
        self._supervision_expired = False
        self._closed = threading.Event()
        self._supervision_thread = None
        self._supervision_config = dict(
            getattr(profile, "configuration", {}).get("buses", {}).get("sim", {}).get("settings", {})
            .get("external_supervision") or {})
        for key in ("timeout_s", "max_observation_age_s", "poll_s"):
            value = self._supervision_config.get(key)
            if self._supervision_config and (type(value) not in (int, float)
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("Invalid simulation supervision setting: " + key)

    def external_supervision_config(self):
        if not self._supervision_config:
            return {"validated": False, "reason": "No simulation supervision profile"}
        scenes = self._supervision_config.get('scenes')
        if scenes and self.binding.configuration.get('scene_profile') not in scenes:
            return {"validated": False, "reason": "Observation timing is not qualified for this scene"}
        return {**self._supervision_config, "validated": True, "scope": "simulation-software"}

    def skill_transition_config(self):
        """Only the measured profile/scene/policy combination may chain skills."""
        from pathlib import Path
        import hashlib
        value = dict(self.binding.configuration.get('skill_transitions') or {})
        if (not self._impl or value.get('validated') is not True or self.binding.mode != 'sim'
                or self.binding.configuration.get('scene_profile') not in value.get('scenes', [])):
            return {'validated':False, 'pairs':[]}
        policy = getattr(self._impl, 'policy', None)
        files = value.get('policy_sha256', {})
        if not policy or set(files) != {'policy.onnx','contract.json','release.yaml'}:
            return {'validated':False, 'pairs':[]}
        try:
            matches = all(hashlib.sha256((Path(policy.dir)/name).read_bytes()).hexdigest() == digest
                          for name,digest in files.items())
        except OSError:
            matches = False
        if matches and self.profile.configuration.get('actuators', {}).get('standing_balance'):
            matches = self._standing_contract_matches(value)
        return {**value, 'validated':matches, 'pairs':value.get('pairs', []) if matches else []}

    def _standing_contract_matches(self, value):
        """Policy weights alone do not qualify changed control code, gains or timing."""
        import hashlib
        import importlib.metadata
        import json
        from pathlib import Path
        profile = self.profile.configuration
        balance = getattr(self._impl, '_balance', None)
        try:
            encoded = json.dumps({key: profile.get(key, {}) for key in ('actuators', 'locomotion', 'sensors')},
                                 sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            if (hashlib.sha256(encoded).hexdigest() != value['profile_sha256'] or balance is None
                    or not math.isclose(balance.physics_dt, value['physics_dt_s'])
                    or not math.isclose(self._impl.policy.policy_dt, value['policy_dt_s'])):
                return False
            implementation = value['implementation_sha256']
            required = {'body/balance.py', 'body/families/humanoid.py', 'body/stopping.py',
                        'body/bus_zmq.py', 'runtime/builtin.py', 'world/mujoco_node.py',
                        'world/composition.py', 'world/bus_server.py', 'world/clearance.py',
                        'body/sensor_state.py', 'world/camera_metadata.py',
                        'world/robot_camera_mounts.py', 'world/own_camera_geometry.py'}
            dependencies = value['dependency_versions']
            if (set(dependencies) != {'mujoco','numpy','onnxruntime'} or any(
                    importlib.metadata.version(name) != version for name,version in dependencies.items())):
                return False
            root = Path(__file__).resolve().parents[1]
            return (set(implementation) == required
                    and all(hashlib.sha256((root / name).read_bytes()).hexdigest() == implementation[name]
                            for name in required))
        except (KeyError, OSError, TypeError, ValueError, importlib.metadata.PackageNotFoundError):
            return False

    def _record_observation(self, started, state, images):
        # Acquisition begins before the bus camera call. A delayed read therefore
        # becomes stale conservatively instead of receiving a fresh return time.
        with self._supervision_lock:
            self._frame_seq += 1
            sequence = self._frame_seq
            if images and not state.get("sensor_errors"):
                self._frames[sequence] = started
            while len(self._frames) > 128:
                self._frames.popitem(last=False)
        return {**state, "frame_seq": sequence, "observed_at": time.time() - (time.monotonic() - started)}, images

    def supervise(self, frame_seq):
        with self._supervision_lock:
            if not self.external_supervision_config().get('validated') or self._closed.is_set():
                return {"ok": False, "message": "Simulation supervision unavailable"}
            acquired = self._frames.get(frame_seq) if type(frame_seq) is int else None
            if (acquired is None or frame_seq <= self._last_ack
                    or time.monotonic() - acquired > self._supervision_config["max_observation_age_s"]):
                return {"ok": False, "message": "Observation is missing, stale or already acknowledged"}
            self._last_ack = frame_seq
            self._supervision_deadline = time.monotonic() + self._supervision_config["timeout_s"]
            self._supervision_expired = False
            if self._supervision_thread is None:
                self._supervision_thread = threading.Thread(target=self._watch_supervision,
                                                            name="nerv-sim-supervision", daemon=True)
                self._supervision_thread.start()
            return {"ok": True, "frame_seq": frame_seq}

    def _watch_supervision(self):
        while not self._closed.wait(self._supervision_config["poll_s"]):
            with self._supervision_lock:
                if (self._supervision_deadline is not None and not self._supervision_expired
                        and time.monotonic() >= self._supervision_deadline):
                    self._supervision_expired = True
                    # Set the stop event while serialized with dispatch. The
                    # current action observes it before its next control step.
                    if self._impl:
                        self.stop.request()

    def supervision_sensors(self):
        return self.sensor_names()

    @property
    def held(self):
        return bool(self._impl and self._impl.held)

    def prepare(self):
        if self._impl is None:
            from ..body import families
            from ..body.bus_zmq import ZmqBus
            from ..body.skills import SkillRunner, StopFlag
            self.stop = StopFlag()
            if (self.profile.configuration.get('actuators', {}).get('standing_balance')
                    and not self.binding.configuration.get('assets_root')):
                raise ValueError('Standing feedback requires the active simulation asset root')
            bus = ZmqBus(self.binding.configuration["bus_url"])
            try:
                self._impl = families.load(self.family).build(
                    {**self.profile.configuration,
                     '_simulation_assets_root': self.binding.configuration.get('assets_root', '')},
                    bus, SkillRunner(self.stop))
            except BaseException:
                bus.close()
                raise
        return {"ok": True, "prepared": True}

    def actions(self):
        return self._impl.tools() if self._impl else []

    def observe(self):
        started = time.monotonic()
        if self._impl is None:
            state, images = self._observe_unprepared()
        else:
            state, images = self._impl.observe()
        return self._record_observation(started, state, images)

    def _observe_unprepared(self):
        """Simulation cameras are readable without constructing motion controllers."""
        import io
        import time
        from PIL import Image
        from ..body.bus_zmq import ZmqBus
        with self._sensor_lock:
            if self._sensor_bus is None:
                self._sensor_bus = ZmqBus(self.binding.configuration["bus_url"])
        state, images = {"prepared": False, "observed_at": time.time()}, []
        for sensor in self.profile.configuration.get("sensors", []):
            name = sensor["name"] if isinstance(sensor, dict) else str(sensor)
            camera = (sensor.get("camera") or name) if isinstance(sensor, dict) else name
            try:
                data, mime, source = self._sensor_bus.sensor_frame(camera)
                if mime != "image/png":
                    if not mime.startswith("image/"):
                        raise ValueError("camera did not return an image")
                    buf = io.BytesIO()
                    Image.open(io.BytesIO(data)).convert("RGB").save(buf, format="PNG")
                    data = buf.getvalue()
                images.append((name, data))
                import hashlib
                state.setdefault('image_frames', {})[name] = {
                    **source, 'source_metadata_available': bool(source),
                    'image_sha256': hashlib.sha256(data).hexdigest()}
            except Exception as error:
                state.setdefault("sensor_errors", {})[name] = f"{type(error).__name__}: {error}"
        return state, images

    def invoke(self, name, _control_guard=nullcontext, **kwargs):
        if self._impl is None:
            return {"ok": False, "message": "prepare the runtime first"}
        from contextlib import contextmanager

        @contextmanager
        def supervised_guard():
            with self._supervision_lock:
                if self._supervision_expired or (self._supervision_deadline is not None
                        and time.monotonic() >= self._supervision_deadline):
                    raise InterruptedError("External supervision expired")
                with _control_guard():
                    yield

        is_skill = bool(getattr(self.binding, 'configuration', {}).get('skill_transitions')) and any(
            action['name'] == name and action.get('kind') == 'skill' for action in self._impl.tools())
        if is_skill:
            # A successful action receipt is historical evidence. Balance may
            # have been lost while the Agent was thinking; check live evidence
            # even when repeating the same skill under a new task or lease.
            evidence = self._impl.status().get('stop_evidence', {})
            if evidence.get('stop_confirmed') is not True:
                return {'ok':False, 'message':'Current stable-stop evidence is unavailable',
                        'data':{'stop_confirmed':False, 'stop_state':'stop_unconfirmed'}}
            if not getattr(self._impl, '_standing_ready', lambda: True)():
                return {'ok':False, 'message':'Standing feedback handover is not complete',
                        'data':{'stop_confirmed':True, 'stop_state':'stopped',
                                'control_ready':False, 'reason':'standing_handover_pending'}}
        if is_skill and not self._last_skill_stopped:
            return {'ok':False, 'message':'Previous motion stop is unconfirmed; operator recovery is required'}
        if is_skill and self._last_skill is not None and self._last_skill != name:
            contract = self.skill_transition_config()
            if (not self._last_skill_stopped or not contract.get('validated')
                    or [self._last_skill, name] not in contract.get('pairs', [])):
                return {'ok':False, 'message':'Runtime skill transition is unqualified or stopping is unconfirmed'}
        self._impl.runner._control.guard = supervised_guard
        try:
            if is_skill:
                self._last_skill, self._last_skill_stopped = name, False
            result = self._impl.invoke(name, **kwargs)
            if is_skill:
                self._last_skill_stopped = result.get('data', {}).get('stop_confirmed') is True
            return result
        finally:
            self._impl.runner._control.guard = nullcontext

    def status(self):
        state = {**self._impl.status(), "prepared": True} if self._impl else {"prepared": False}
        with self._supervision_lock:
            state["external_supervision"] = {
                "last_acknowledged_frame": self._last_ack,
                "expired": self._supervision_expired,
                "active": self._supervision_deadline is not None and not self._supervision_expired,
            }
        return state

    def execution_status(self):
        return {"running": bool(self._impl and self._impl.runner.running),
                "execution": self._impl.runner.running if self._impl else ""}

    def sensor_names(self):
        return (self._impl.sensor_names() if self._impl else [
            s["name"] if isinstance(s, dict) else str(s)
            for s in self.profile.configuration.get("sensors", [])])

    def stream_jpeg(self):
        return self._impl.stream_jpeg() if self._impl else None

    def sensor_jpeg(self, name):
        from nerv_runtime_sdk.node import jpeg_from_png
        _state, images = self.observe()
        return next((jpeg_from_png(png) for key, png in images if key == name and png), None)

    def config_options(self):
        return self._impl.config_options() if self._impl else []

    def set_option(self, key, value):
        return self._impl.set_option(key, value) if self._impl else {"ok": False, "message": "not prepared"}

    def cancel_current(self, reason="cancelled"):
        # BodyNode revokes the old token before cancellation. Invalidate its
        # observation receipts as well so manual takeover is not held behind an
        # expired external lease, and a later Agent needs a new observation.
        with self._supervision_lock:
            if self._impl:
                self.stop.request()
            self._frames.clear()
            self._last_ack = self._frame_seq
            self._supervision_deadline = None
            self._supervision_expired = False
        if self._impl is None:
            return {"ok": True, "accepted": True, "dispatch_revoked": True,
                    "stop_confirmed": False, "stop_state": "offline"}
        cancel = getattr(self._impl, 'cancel_current', self._impl.hold)
        result = cancel(reason)
        return {**result, "accepted": True, "dispatch_revoked": True}

    def hold(self, reason="operator"):
        if self._impl:
            self.stop.request()
        return self._impl.hold(reason) if self._impl else {"ok": False, "held": False}

    def release(self):
        stopped = bool(self._impl and self._impl.status().get('stop_evidence', {}).get('stop_confirmed'))
        result = self._impl.release() if self._impl else {"ok": False}
        if result.get('ok') and stopped:
            # Recovery may clear an unconfirmed stop, never erase the prior skill
            # and bypass the qualified transition contract.
            self._last_skill_stopped = True
        return result

    def close(self):
        self._closed.set()
        if self._supervision_thread:
            self._supervision_thread.join(timeout=2)
        if self._impl:
            self.stop.request()
        if self._sensor_bus:
            self._sensor_bus.close()
        if self._impl:
            self._impl.close()
