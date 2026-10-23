"""Host arbitration for simulation-only world control; no world-specific properties."""
import uuid

from jsonschema import validate

from ..world.configuration import resolve_configuration


class WorldControls:
    def __init__(self, hub):
        self.hub = hub

    def _binding(self, sid):
        session = self.hub.store.get(sid)
        if session.mode != "sim" or not session.world:
            raise ValueError("World control is available only in simulation")
        if error := self.hub._control_error(session):
            raise ValueError(error["message"])
        world = self.hub.world_client(session.world, session.body)
        if world is None:
            raise ValueError("World unavailable")
        return session, world

    def capabilities(self, sid):
        session, world = self._binding(sid)
        descriptor = world.control_get()
        config = self.hub.registry.world(session.world).configuration
        descriptor["configuration"] = config.get("schema", {})
        if config.get("schema", {}).get("properties"):
            descriptor["commands"].append({"name": "rebuild", "description":
                "End this instance and create a new one with these properties; old tasks are cancelled.",
                "parameters": config["schema"], "effect": "rebuild"})
        return descriptor

    def invoke(self, sid, name, arguments, *, instance_id=None, revision=None, current=lambda: True):
        session, world = self._binding(sid)
        with self.hub._node_lock:
            if not current():
                raise ValueError("Agent decision superseded")
            descriptor = self.capabilities(sid)
            if instance_id is not None and instance_id != session.epoch:
                raise ValueError("World instance changed")
            if revision is not None and revision != descriptor["revision"]:
                raise ValueError("World revision changed")
            command = next((c for c in descriptor["commands"] if c["name"] == name), None)
            if command is None:
                raise ValueError("World does not declare this operation")
            validate(arguments, command["parameters"])
            self.hub.tasks.cancel(sid, reason="World intervention", strict=True)
            self.hub.store.set_armed(sid, False)
            robot = self.hub.body_client(session.body)
            robot.set_config("armed", "false")
            self.hub.store.memory(sid).append("world_control_requested", {
                "instance_id": session.epoch, "name": name, "arguments": arguments})
            if not current():
                raise ValueError("World change cancelled before application")
            if name == "rebuild":
                values = resolve_configuration(self.hub.registry.world(session.world).configuration, arguments)
                handle = self.hub.launcher.nodes[f"body:{session.body}"]
                self.hub.stop_simulation(handle.name, session.world, handle.meta["epoch"], sid)
                created = self.hub.new_session(session.brain, session.body, session.world,
                    session.sensors, session.agent_tools, "sim", values)
                result = {"ok": True, "message": "New instance created in standby", "session": created}
            else:
                result = world.control_post({"instance_id": session.epoch,
                    "revision": descriptor["revision"], "request_id": uuid.uuid4().hex,
                    "command": name, "arguments": arguments})
            self.hub.store.memory(sid).append("world_control_result", {"name": name, **result})
            self.hub.store.memory(sid).append("robot_activity", {"instance_id": session.epoch,
                "task_id": None, "version": None, "source": "host", "type": "world_control",
                "text": result.get("message", "World change finished"), "ok": result.get("ok")})
            return result
