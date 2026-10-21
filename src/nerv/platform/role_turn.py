"""Role-specific host links. Provider function calling is only a transport."""
from __future__ import annotations

import time
import threading

from jsonschema import validate

from ..brain import prompts
from ..nerve.body import ActionResult
from ..nerve.brain import CallTool, SetRegister, ToolResult
from ..nerve.capabilities import Capability, CapabilityRegistry
from .router import Turn


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {},
            "required": list(required), "additionalProperties": False}


class RoleTurn(Turn):
    domain_capabilities = True

    def __init__(self, hub, session, brain, emit, token, *, role="agent", store=None):
        self.hub, self.role, self.token = hub, role, token
        self.robot = hub.body_client(session.body) if session.body else None
        world = hub.world_client(session.world, session.body) if session.world and session.body else None
        nodes = [hub.tool_client(n) for n in session.agent_tools]
        def emit_business_event(event):
            # The typed capability receipts already cover these transport events.
            if event.get("type") not in {"tool_call", "tool_result"}:
                emit(event)
        super().__init__(store or hub.store, session, brain,
                         self.robot if role == "system2" else None, world,
                         [n for n in nodes if n] if role == "agent" else [], hub.gate, emit_business_event,
                         world_name=session.world or "", current=lambda: not token.is_set(),
                         guard=threading.RLock(), control_token=getattr(token, "control_token", None))
        self.catalog = CapabilityRegistry()
        self._native_names = {}

    def call_identity(self, name):
        try:
            cap = self.catalog.resolve(name)
            return {"domain": cap.domain, "capability_name": cap.name}
        except ValueError:
            return {}

    def is_current(self):
        return (not self.token.is_set() and not self._stop and time.monotonic() < self.deadline
                and self.hub.store.exists(self.session.id)
                and self.hub.store.is_active(self.session.id))

    def stop_reason(self):
        if self.token.is_set():
            return "superseded"
        if self._stop:
            return self._stop
        if self.step >= self.max_steps or time.monotonic() >= self.deadline:
            self._stop = "steps" if self.step >= self.max_steps else "time"
            if self.role == "system2" and self.robot:
                self.robot.post("/stop")
            return self._stop
        return None

    def observe(self):
        if self.role == "system2":
            return super().observe()
        self.step += 1
        if self.robot is None or not self.is_current():
            return None
        # Read only the robot sensor interface. Chat is not an execution supervisor
        # and must never acknowledge supervision or stop an independent task.
        return self.record_observation(self.robot.perceive())

    def executing(self):
        return self.role == "system2" and super().executing()

    def system_prompt(self):
        from . import role_prompts
        prefix = role_prompts.SYSTEM2 if self.role == "system2" else role_prompts.AGENT
        import json
        return prefix + "\nCurrent registers: " + json.dumps(self.registers(), ensure_ascii=False) + (
            "\nRobot: " + str(self.session.body) + "\nEnvironment: " + self.session.mode) + (
            "\nRuntime guidance: " + json.dumps(self.system_context(), ensure_ascii=False)
            if self.role == "system2" else "")

    def _add(self, domain, name, description, parameters, kind="read", route=None):
        key = self.catalog.register(Capability(domain, name, description, parameters, kind))
        self._native_names[key] = name
        if route:
            self._routes[key] = route

    def tools(self):
        if self._tools is not None:
            return self._tools
        self._routes = {}
        if self.robot:
            for spec in self.robot.capabilities().operations:
                self._add("robot_actions", spec.name, spec.description, spec.parameters, spec.kind,
                          ("body", self.robot, spec))
        for node in self.tool_nodes:
            for spec in node.capabilities().operations:
                self._add("agent_tools", node.name + "/" + spec.name, spec.description,
                          spec.parameters, spec.kind, ("tool", node, spec))
                # External nodes receive their native function name.
                self._native_names[next(reversed(self._native_names))] = spec.name
        for td in (prompts.HISTORY_SEARCH_TOOL, prompts.HISTORY_READ_TOOL):
            from ..nerve.body import ToolSpec
            spec = ToolSpec(td["name"], td["description"], td["parameters"], "read")
            self._add("agent_tools", spec.name, spec.description, spec.parameters,
                      route=("memory", self, spec))
        for td in (prompts.CORE_TASK_SET_TOOL, prompts.CORE_TASK_CLEAR_TOOL,
                   prompts.NOTE_ADD_TOOL, prompts.NOTE_DROP_TOOL):
            self._add("agent_tools", td["name"], td["description"], td["parameters"])
        if self.role == "agent":
            for name, description, schema in self.hub.tasks.capabilities():
                self._add("robot_tasks", name, description, schema)
            if self.session.mode == "sim" and self.session.world and hasattr(self.hub, "world_controls"):
                for item in self.hub.world_controls.capabilities(self.session.id).get("commands", []):
                    self._add("world_control", item["name"], item["description"], item["parameters"])
            self._add("instance_control", "status", "Read the bound instance lifecycle status.", object_schema())
            if self.session.mode == "sim" and self.session.body:
                self._add("instance_control", "end", "End the owned simulation and freeze its old sessions.", object_schema())
            if not self.session.body:
                self._add("instance_control", "create", "Create a simulation instance. Never prepares real hardware.",
                    object_schema({"robot": {"type": "string", "enum": list(self.hub.registry.bodies)},
                                   "world": {"type": "string", "enum": [w.name for w in self.hub.registry.worlds.values() if w.kind == "sim"]}}, ["robot", "world"]))
        self._tools = self.catalog.transport()
        return self._tools

    def call_tool(self, call: CallTool):
        self.tools()
        try:
            cap = self.catalog.resolve(call.name)
            validate(call.arguments, cap.parameters)
            if not self.is_current():
                raise ValueError("request was superseded")
            self.memory().append("capability_call", {"call_id": call.call_id, "domain": cap.domain,
                                                    "name": cap.name, "arguments": call.arguments})
            self.emit({"type": "capability_call", "domain": cap.domain, "name": cap.name,
                       "args": call.arguments})
            if cap.domain == "robot_tasks":
                result = self.hub.tasks.invoke(self.session.id, cap.name, call.arguments, current=self.is_current)
            elif cap.domain == "world_control":
                result = self.hub.world_controls.invoke(self.session.id, cap.name, call.arguments, current=self.is_current)
                if result.get("session"):
                    self.emit({"type": "instance_created", "session_id": result["session"]["id"]})
            elif cap.domain == "instance_control":
                if cap.name == "status":
                    result = {"ok": True, "data": self.hub.store.get(self.session.id).summary()}
                elif cap.name == "create":
                    created = self.hub.new_session(self.session.brain, call.arguments["robot"], call.arguments["world"], tools=self.session.agent_tools)
                    result = {"ok": True, "data": {"session": created}, "message": "Simulation created in standby"}
                    self.emit({"type": "instance_created", "session_id": created["id"]})
                else:
                    handle = self.hub.launcher.nodes[f"body:{self.session.body}"]
                    result = self.hub.stop_simulation(handle.name, self.session.world, handle.meta["epoch"], self.session.id)
            elif cap.domain == "agent_tools" and cap.name in prompts.META_TOOL_NAMES:
                # Register handlers use native names but retain the provider call identity.
                native = CallTool(call.call_id, cap.name, call.arguments)
                result = super().set_register(SetRegister(cap.name, {**native.arguments, "call_id": call.call_id}))
                return result
            elif cap.domain == "agent_tools" and self._routes[call.name][0] == "memory":
                memory = self.memory()
                data = (memory.search(str(call.arguments.get("query", "")), int(call.arguments.get("limit", 10)))
                        if cap.name == prompts.HISTORY_SEARCH_TOOL["name"] else
                        memory.read_range(int(call.arguments.get("start", 1)), int(call.arguments.get("limit", 20))))
                data = [{**e, "data": {k: v for k, v in e["data"].items() if k != "continuation"}}
                        for e in data if e["kind"] == "message"]
                result = {"ok": True, "data": {"events": data}, "message": "Scoped history"}
            else:
                if cap.domain == "robot_actions" and self.role == "agent" and cap.kind != "read":
                    if cap.kind == "skill":
                        result = self.hub.tasks.start(self.session.id,
                            f"Execute the specified skill {cap.name} with arguments {call.arguments}. "
                            "Supervise using fresh observations; do not claim success without evidence.", current=self.is_current)
                        return self._finish(call, ActionResult(result.get("ok", False), result.get("message", ""), result))
                    self.hub.tasks.cancel(self.session.id, reason="direct action takeover", strict=True)
                    self.control_token = self.hub.claim_robot(self.session.id)
                    if not self.is_current():
                        raise ValueError("action cancelled before dispatch")
                    # This explicit request observes only when needed for robot execution.
                    self.body = self.robot
                    try:
                        super().observe()
                        return super().call_tool(call)
                    finally:
                        self.body = None
                return super().call_tool(call)
            return self._finish(call, ActionResult(bool(result.get("ok", True)), result.get("message", ""),
                                                   result.get("data", result)))
        except Exception as error:
            return self._finish(call, ActionResult(False, str(error)))

    def _finish(self, call, res):
        # Native register handlers enter here too; restore the registered wire name.
        for wire, cap in self.catalog._entries.items():
            if cap.domain == "agent_tools" and cap.name == call.name:
                call = CallTool(call.call_id, wire, call.arguments)
                break
        if not self.is_current():
            return ToolResult(call.call_id, call.name, False, "Request superseded")
        try:
            cap = self.catalog.resolve(call.name)
        except ValueError:
            return super()._finish(call, res)
        self.memory().append("capability_result", {"call_id": call.call_id, "domain": cap.domain,
                                                  "name": cap.name, "ok": res.ok, "message": res.message})
        self.emit({"type": "capability_result", "domain": cap.domain, "name": cap.name,
                   "ok": res.ok, "message": res.message,
                   "execution_id": (res.data or {}).get("execution_id") or (res.data or {}).get("id")})
        return super()._finish(call, res)
