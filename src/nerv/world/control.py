"""Simulation property/command boundary, separate from robot actions and observations."""
import threading

from jsonschema import validate

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


class WorldController:
    def __init__(self, sim):
        self.sim, self.revision = sim, 0
        self.lock = threading.RLock()
        self.receipts = {}

    def describe(self):
        commands = [{"name": "reset", "description": "Restore this instance's initial state and selected objects.",
                     "parameters": EMPTY, "effect": "invalidate_observations"}]
        if self.sim.scene_operator:
            commands.append({"name": "time", "description": "Change the simulation lighting phase.",
                "parameters": {"type": "object", "properties": {"phase": {"type": "string",
                    "enum": list(self.sim.scene_operator.runtime.phases)}}, "required": ["phase"],
                    "additionalProperties": False}, "effect": "invalidate_observations"})
        return {"domain": "world_control", "mode": "sim", "instance_id": self.sim.epoch,
                "revision": self.revision, "commands": commands,
                "state": getattr(self.sim, "instance_receipt", {})}

    def invoke(self, request):
        with self.lock:
            if request.get("instance_id") != self.sim.epoch:
                raise ValueError("World instance changed")
            request_id = request.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("A request ID is required")
            if request_id in self.receipts:
                previous, receipt = self.receipts[request_id]
                if previous != request:
                    raise ValueError("Request ID was reused with different contents")
                return receipt
            if request.get("revision") != self.revision:
                raise ValueError("World revision changed; refresh before applying")
            command = next((c for c in self.describe()["commands"] if c["name"] == request.get("command")), None)
            if command is None:
                raise ValueError("World does not declare this command")
            arguments = request.get("arguments", {})
            validate(arguments, command["parameters"])
            if command["name"] == "reset":
                self.sim.reset()
            elif command["name"] == "time":
                def apply(renderer):
                    with self.sim._lock:
                        self.sim.scene_operator.revoke("world_control")
                        self.sim.scene_operator.runtime.set_time(arguments["phase"], renderer)
                self.sim.render.run(apply)
            self.revision += 1
            result = {"ok": True, "instance_id": self.sim.epoch, "revision": self.revision,
                      "message": "World change applied; prior observations invalidated"}
            self.receipts[request_id] = (dict(request), result)
            return result
