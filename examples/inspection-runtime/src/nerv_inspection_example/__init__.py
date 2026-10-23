"""Read-only protocol example. It never connects to hardware or simulates a robot."""
from nerv_runtime_sdk.contracts import RuntimeManifest


class Provider:
    def describe(self):
        return RuntimeManifest(id="inspection", version="0.1.0", features=["read_only"],
                               configuration_schema={"type": "object", "additionalProperties": False})

    def create(self, profile, binding):
        if binding.configuration:
            raise ValueError("This inspection example accepts no device configuration")
        return Inspector(profile)


class Inspector:
    version = "0.1.0"
    held = False

    def __init__(self, profile):
        self.name, self.family = profile.id, profile.family
        self.prepared = False

    def prepare(self):
        self.prepared = True
        return {"ok": True, "prepared": True}

    def actions(self):
        return [{"name": "inspect", "kind": "read", "description": "Read example lifecycle state; no robot is attached.",
                 "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}]

    def sensor_names(self):
        return []

    def observe(self):
        return self.status(), []

    def invoke(self, name, **arguments):
        if name != "inspect":
            return {"ok": False, "message": "Unknown action"}
        return {"ok": True, "message": "Inspection-only example", "data": self.status()}

    def status(self):
        return {"prepared": self.prepared, "physical_device": False}

    def execution_status(self):
        return {"running": False}

    def cancel_current(self, reason="cancelled"):
        return {"ok": True, "message": "No actuator or active execution"}

    def hold(self, reason="operator"):
        self.held = True
        return {"ok": True, "held": True}

    def release(self):
        self.held = False
        return {"ok": True, "held": False}

    def config_options(self):
        return []

    def set_option(self, key, value):
        return {"ok": False, "message": "No configurable options"}

    def close(self):
        self.prepared = False
