"""Adapter for existing simulated families; construction has no simulation side effects."""
from __future__ import annotations

from contextlib import nullcontext
import threading

from nerv_runtime_sdk.contracts import RuntimeManifest


class BuiltinProvider:
    def describe(self):
        return RuntimeManifest(id="builtin", version="0.6.2", features=["sim", "named_sensors"])

    def create(self, profile, binding):
        if binding.mode != "sim":
            raise ValueError("the builtin adapter only handles simulation")
        return BuiltinRuntime(profile, binding)


class BuiltinRuntime:
    guarded_dispatch = True
    def __init__(self, profile, binding):
        self.profile, self.binding = profile, binding
        self.name, self.family, self.version = profile.id, profile.family, "0.6.2"
        self._impl = None
        self._sensor_bus = None
        self._sensor_lock = threading.Lock()

    @property
    def held(self):
        return bool(self._impl and self._impl.held)

    def prepare(self):
        if self._impl is None:
            from ..body import families
            from ..body.bus_zmq import ZmqBus
            from ..body.skills import SkillRunner, StopFlag
            self.stop = StopFlag()
            bus = ZmqBus(self.binding.configuration["bus_url"])
            self._impl = families.load(self.family).build(
                self.profile.configuration, bus, SkillRunner(self.stop))
        return {"ok": True, "prepared": True}

    def actions(self):
        return self._impl.tools() if self._impl else []

    def observe(self):
        if self._impl is None:
            return self._observe_unprepared()
        import time
        state, images = self._impl.observe()
        return {**state, "observed_at": time.time()}, images

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
                data, mime = self._sensor_bus.sensor(camera)
                if mime != "image/png":
                    if not mime.startswith("image/"):
                        raise ValueError("camera did not return an image")
                    buf = io.BytesIO()
                    Image.open(io.BytesIO(data)).convert("RGB").save(buf, format="PNG")
                    data = buf.getvalue()
                images.append((name, data))
            except Exception as error:
                state.setdefault("sensor_errors", {})[name] = f"{type(error).__name__}: {error}"
        return state, images

    def invoke(self, name, _control_guard=nullcontext, **kwargs):
        if self._impl is None:
            return {"ok": False, "message": "prepare the runtime first"}
        self._impl.runner._control.guard = _control_guard
        try:
            return self._impl.invoke(name, **kwargs)
        finally:
            self._impl.runner._control.guard = nullcontext

    def status(self):
        return {**self._impl.status(), "prepared": True} if self._impl else {"prepared": False}

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
        if self._impl:
            self.stop.request()
        return {"ok": True, "message": "Stop forwarded to the active simulation runner"}

    def hold(self, reason="operator"):
        if self._impl:
            self.stop.request()
        return self._impl.hold(reason) if self._impl else {"ok": False, "held": False}

    def release(self):
        return self._impl.release() if self._impl else {"ok": False}

    def close(self):
        if self._sensor_bus:
            self._sensor_bus.close()
        if self._impl:
            self._impl.close()
