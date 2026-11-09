# Brain Core in NERV 0.9.1

This is an architecture and compatibility change under the requested 0.9.1 version. The implementation is a local development candidate. Independent dual-Agent navigation acceptance has not yet passed.

## Responsibility

External Agents own goal interpretation, visual reasoning, planning, model calls and conversation context. Brain Core ships inside NERV and owns identity bindings, sensor receipts, task access, authority and execution records. Runtime owns drivers, ACT/gait inference, control loops and local stopping. Brain Core neither installs as a separate product nor hosts an AI Agent.

The active implementation is `nerv.brain_core`. Historical `nerv.brain`, `nerv.harness` and `nerv.vlm` source remains available for preservation, but wheel and npm packages exclude it. Old chat/model/credential mutation endpoints return retirement errors. A model credential is unnecessary for operator or fixed-policy execution. Asset-download credentials have a separate store; upgrading does not copy model keys.

## Binding and transport

A Project continues to represent one controlled robot. The operator reserves a Run and Brain for an explicitly paired client. A Run pins its Project revision; its Brain, robot and world identifiers remain stable. `run_start` starts that reserved Run. It cannot create an extra robot or choose another binding. A legacy MCP command without `--brain` is accepted only if the client has exactly one active binding.

Start the bridge with `nerv mcp --channel CHANNEL --brain BRAIN_ID --client CLIENT_ID`. `brain_get` supplements the fourteen existing tools. It returns the binding, Run state, control state and recovery instruction. Receipts carry Brain, Run, robot and world IDs, binding/world generations and available Runtime/world epochs. Idempotent retries retain their original execution metadata.

The stdio bridge registers a transport generation and sends bounded liveness heartbeats. A heartbeat never confirms an image. A second simultaneous connection cannot take the same Brain. Disconnect or expiry revokes that Brain's control; reconnecting preserves read access but requires new operator control assignment and fresh observation before motion. A late acknowledgement cannot restore a revoked generation.

## Shared World

The operator freezes a roster and explicit spawn poses before starting. Scene inputs are fingerprinted before launch and rechecked at preparation. The source compiler attaches namespaced robot trees to one MuJoCo model and data object. All robots share the physics clock and contacts; each has its own bus, Runtime, actuator map, camera namespace, control target and journal.

No robot endpoint can reset the World. Ending A's Run stops/disarms A while retaining its physical body and B's simulation. Operator World stop invalidates every associated Run. Runtime process loss invalidates the affected Run; loss of the shared physics process invalidates all its running members. No recovery path replays motion.

The current composition path requires a common registered scene and robot profile. Dynamic topology and nonempty scene-configuration overrides are not yet supported. These are explicit restrictions, not silently ignored inputs.

## Execution and qualification

The model-free dispatcher supports external primitives, external skills and a fixed policy. New tasks cannot erase the previous skill in the same robot instance. Runtime additionally qualifies G1 skill transitions against the scene and exact policy/contract/release hashes. The current development contract covers the existing G1 turning policy in `apt`; it does not qualify Go2, arbitrary policies, ACT transitions or real devices.

CPU development probes executed fourteen concurrent forward/turn actions across two G1 instances, with fourteen stable-stop confirmations and no reported falls. In that probe, six 0.5 m forward requests had final errors from 0.003 to 0.021 m, and eight 45-degree turns had errors from 0.5 to 3.3 degrees. These observations support a scoped transition contract; they do not establish independent navigation success or a broad reliability rate.

G1 requests below 0.5 m are rejected before motion because smaller scales have not been qualified. Approach speed remains within the existing policy command range. Results retain actual displacement, requested target, target error, obstruction, fall, timeout and stop confirmation. `travel_threshold_crossed` is a local control condition; `target_reached=null` does not claim arrival without a qualified tolerance or independent task evaluator.

Waiting for an external Agent expires an old observation without inventing acknowledgement or cancelling an idle external task. Motion remains subject to Runtime deadlines. The G1 apt CPU contract `g1-apt-cpu-mcp-091-1` allows 40 seconds from image acquisition to explicit acknowledgement and retains a separate 20-second motion-supervision deadline. Six independent MCP image-processing probes measured 11.08–19.96 seconds from acquisition to acknowledgement; the age bound is twice the measured maximum, rounded upward to 5 seconds. This is scoped to apt simulation. Installed-candidate navigation and deadline fault trials remain outstanding.

## Migration and recovery

Historical managed/internal-decision projects retain their configuration and journals and require a new external scheme. They do not silently gain external control. Fixed-policy projects retain their execution route. Historical chats remain readable; no model is called to migrate or compact them. Project/Run/Task records remain the authoritative history.

Use an isolated data home for this candidate. Public release, stable installation replacement and remote deployment are separate actions. Preserve the original data baseline and prior candidate evidence when testing upgrades or recovery.
