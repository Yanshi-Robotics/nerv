# NERV Runtime SDK

The SDK runs in the adapter's Python environment. NERV Host does not import vendor SDKs,
PyTorch or device objects. Non-Python adapters can serve the equivalent HTTP/MCP protocol.

Protocol **2.0** separates `robot_actions` from Agent auxiliary tools. A `RuntimeProvider`
registers under the `nerv.runtime_providers` Python entry-point group. `describe()` returns
identity, version, configuration schema and source fingerprints without touching devices.
The worker checks protocol compatibility before calling `create()` or preparing a device.

## Provider lifecycle

1. Install the adapter and this SDK in its own environment. Run `nerv-runtime discover`.
2. Register a robot profile and a sim or real environment binding in NERV's registry.
3. Use `nerv-runtime check --provider <provider-id>` for metadata consistency. Add
   `--url <worker-url>` to inspect a running worker without preparing or enabling it.
4. Prepare through the operator interface. Report actual sensors, actions, policy formats,
   version fingerprints and prepared state. Loading policy weights must not dispatch motion.
5. Execute only while armed and holding the current control token. Check dispatch authority
   at every actuator write, including after model inference. A stale token must send zero actions.
6. Cancel locally and report confirmation separately from the request. Hold, release, reset,
   disconnect and torque release are different operations. Release never resumes an old task.

`RobotRuntime.actions()` declares read operations, bounded primitives and persistent skills.
Sensors use `sensor_names()` and `observe()`; they are not actions. `/executions/status`
reports active execution independently of a chat request. Runtime supervision and emergency
controls must remain available while the network or a provider request is blocked.

The worker uses MCP function transport for actions and resources for observations and
capability metadata. Its capability resource contains `robot_actions`, not a mixed business
`tools` field. The host maps exact registered identities into provider function names.

## Reference implementations

- [Inspection-only provider](../../examples/inspection-runtime/): minimal installable example;
  no device, actuator or synthetic success.
- [Simulation adapter](../../src/nerv/runtime/builtin.py): wraps existing family controllers.
- [LeRobot adapter](../nerv-runtime-lerobot/src/nerv_runtime_lerobot/): isolated device, shared
  capture, ACT execution and local supervision.
- [Contracts](src/nerv_runtime_sdk/contracts.py) and [dispatch authority](src/nerv_runtime_sdk/control.py).

A protocol check is not a physical acceptance test. Existing protocol 1 workers must be
upgraded; the host does not silently downgrade to the mixed capability contract.
