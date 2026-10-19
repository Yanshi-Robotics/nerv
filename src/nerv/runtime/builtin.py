"""Adapter for existing simulated families; construction has no simulation side effects."""
from __future__ import annotations

from contextlib import nullcontext

from nerv_runtime_sdk.contracts import RuntimeManifest


class BuiltinProvider:
    def describe(self):
        return RuntimeManifest(id="builtin", version="0.6.0", features=["sim", "named_sensors"])

    def create(self, profile, binding):
        if binding.mode != "sim":
            raise ValueError("the builtin adapter only handles simulation")
        return BuiltinRuntime(profile, binding)


class BuiltinRuntime:
    guarded_dispatch = True
    def __init__(self, profile, binding):
        self.profile, self.binding = profile, binding
        self.name, self.family, self.version = profile.id, profile.family, "0.6.0"
        self._impl = None

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

    def tools(self):
        return self._impl.tools() if self._impl else []

    def observe(self):
        if self._impl is None:
            return {"prepared": False}, []
        import time
        state, images = self._impl.observe()
        return {**state, "observed_at": time.time()}, images

    def invoke(self, name, _control_guard=nullcontext, **kwargs):
        if self._impl is None:
            return {"ok": False, "message": "prepare the runtime first"}
        self._impl.runner._control.guard = _control_guard
        try:
            return self._impl.invoke(name, **kwargs)
        finally:
            self._impl.runner._control.guard = nullcontext

    def status(self):
        return self._impl.status() if self._impl else {"prepared": False}

    def sensor_names(self):
        return self._impl.sensor_names() if self._impl else []

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
        if self._impl:
            self._impl.close()
