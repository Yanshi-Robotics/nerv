# Brain Core in NERV 0.9.1

This is an architecture and compatibility change under the requested 0.9.1 version. This completion revision is source development, not a newly installed candidate. Independent dual-Agent navigation acceptance has not yet passed.

## Responsibility

External Agents own goal interpretation, visual reasoning, planning, model calls and conversation context. Brain Core ships inside NERV and owns identity bindings, sensor receipts, task access, authority and execution records. Runtime owns drivers, ACT/gait inference, control loops and local stopping. Brain Core neither installs as a separate product nor hosts an AI Agent.

The active implementation is `nerv.brain_core`. Historical `nerv.brain`, `nerv.harness` and `nerv.vlm` source remains available for preservation, but wheel and npm packages exclude it. Old chat/model/credential mutation endpoints return retirement errors. A model credential is unnecessary for operator or fixed-policy execution. Asset-download credentials have a separate store; upgrading does not copy model keys.

## Persistent resources and transport

World, Robot and canonical Brain identities are stored independently of Runs. The operator can create and start an empty World. An allocation grant specifies a compatible Project template, instance quota, private spawn slots and whether autonomous simulation control acquisition is permitted. The unbound bridge exposes `platform_describe`, `robot_allocate`, `operation_get`, `brain_attach` and `robot_remove`. Allocation retries are scoped to principal and request ID; different parameters cannot reuse an ID. Allocation and removal return a durable operation ID before physical work finishes. Poll `operation_get` until complete, failed or interrupted. A failed operation retains any allocated identity; a Host restart marks unfinished operations interrupted without replaying them. Operator recovery is required for an unknown physical outcome.

After `brain_attach`, MCP announces a changed tool list and scopes control to that Brain. `brain_get` describes identity, Run, resource and control state. `control_acquire` requires an existing operator pregrant and a fresh observation; `control_release` revokes ownership and requests stopping. Multiple authorized observers may connect, but one Agent owns motion. Observer disconnect does not revoke another connection's control. Reconnection restores reading only. Runtime checks local control deadlines independently of transport heartbeat.

Observations and capability readiness are readable without a Run. Such reads carry resource identities and Runtime generation, but no motion receipt or authority. A changed Runtime epoch or a concurrent topology/resource change rejects the read. The console can display these resource observations and has separate device connection controls.

## Dynamic shared World

The source compiler creates namespaced robot trees in one MuJoCo model/data pair. A topology transaction quiesces members under the operator policy, pauses physical advancement, builds a separate candidate, migrates named joint and scene state, reconstructs mappings and publishes a new revision. Missing or stale topology revisions cannot drive actuators after a change. Input assets and third-party robot files are not edited.

A failed candidate compile preserves the prior physical objects. Once a topology has committed, Runtime preparation failure is explicitly recovery-required; operator stop/restart is required. This is not a claim of transparent rollback. Activation, equality, mocap and plugin states are rejected by the current migration implementation rather than silently lost.

Ending a shared Run keeps the Robot, Brain and World. Entity removal is separate and requires the Run to end. Host restart retains identities but interrupts attachments and motion authority. An explicit operator World restart changes generation/epoch; it does not restore tasks or motion. Registered devices use separate operator connect/disconnect operations. Ending a device Run retains its Runtime attachment; disconnecting A does not close B or a simulation World. Inert-adapter regressions cover this lifecycle, failed preparation and restart invalidation. They do not establish real-device support. After a restart, reconnect is explicit and no motion is replayed.

## Completion qualification status

Current source checks cover resource allocation, scoped retries, observer isolation, dynamic generic physics and MCP tool-list changes. Bounded G1 CPU diagnostics have exercised A → A+B → B → empty World. After removing A, B executed a 0.5 m request with approximately 0.012 m measured forward error, then confirmed stopping; the empty world's clock continued. These are direct Runtime diagnostics, not installed-candidate or independent Agent acceptance.

A static target hold reproduced a fall after a successful motion chain. Active zero-command balance avoided that fall in short probes, but a two-minute test revealed slow drift and revoked stop confirmation for both robots. A separate single-robot idle test also reproduced the drift, so topology change is not a necessary cause. A separate World process over the real ZMQ IPC bus also reproduced drift over 120 seconds. Long holding remains unqualified and the skill-transition validation flag is disabled. No stopping threshold was relaxed.

Physics evidence can now be sampled at the physical step, with robot contacts and falls recorded separately from rendering. The new paired scorer requires complete data, a common stable window of at least three seconds, bounded drift/distance, current stopping evidence and three independently reviewed images from each robot. Calibration numbers and formal rendezvous trials remain pending. The historical findings below are retained as results of their original candidates.

## Execution and qualification

The model-free dispatcher supports external primitives, external skills and a fixed policy. New tasks cannot erase the previous skill in the same robot instance. Runtime additionally qualifies G1 skill transitions against the scene and exact policy/contract/release hashes. The previous development contract covered the existing G1 turning policy in `apt`; it does not qualify Go2, arbitrary policies, ACT transitions or real devices.

Earlier CPU development probes executed fourteen concurrent forward/turn actions across two G1 instances, with fourteen stable-stop confirmations and no reported falls. In that probe, six 0.5 m forward requests had final errors from 0.003 to 0.021 m, and eight 45-degree turns had errors from 0.5 to 3.3 degrees. These observations support a scoped transition contract; they do not establish independent navigation success or a broad reliability rate.

G1 requests below 0.5 m are rejected before motion because smaller scales have not been qualified. Approach speed remains within the existing policy command range. Results retain actual displacement, requested target, target error, obstruction, fall, timeout and stop confirmation. `travel_threshold_crossed` is a local control condition; `target_reached=null` does not claim arrival without a qualified tolerance or independent task evaluator.

Waiting for an external Agent expires an old observation without inventing acknowledgement or cancelling an idle external task. Motion remains subject to Runtime deadlines. The G1 apt CPU contract `g1-apt-cpu-mcp-091-2` allows 40 seconds from image acquisition to explicit acknowledgement and retains a separate 20-second motion-supervision deadline. Six independent MCP image-processing probes measured 11.08–19.96 seconds from acquisition to acknowledgement; the age bound is twice the measured maximum, rounded upward to 5 seconds. This is scoped to apt simulation. Installed-candidate navigation and transport-loss trials have now run; the findings below distinguish protocol checks from failed task acceptance.

## Migration and recovery

Historical managed/internal-decision projects retain their configuration and journals and require a new external scheme. They do not silently gain external control. Fixed-policy projects retain their execution route. Historical chats remain readable; no model is called to migrate or compact them. Project/Run/Task records remain the authoritative history.

Use an isolated data home for this candidate. Public release, stable installation replacement and remote deployment are separate actions. Preserve the original data baseline and prior candidate evidence when testing upgrades or recovery.

Normal G1 action completion may wait up to 4 simulation seconds for the existing 0.3 rad/s, 0.2-second quiet criterion. This is distinct from the unchanged 1.5-second emergency hold deadline. An installed-candidate 30-degree turn exhausted the prior normal-action wait; retained truth showed a qualifying quiet interval beginning about 0.09 seconds after that response. The new finite wait is the rounded-up double of the observed approximately 1.79-second settling requirement. It does not change velocity commands, gait weights, the quiet threshold, or the 20-second supervision deadline. An unconfirmed stop still blocks the next motion. This calibration alone is not navigation acceptance.


## Installed candidate findings

Candidate 08, built from `248c7c0`, passed 713 CPU software tests with two retired-provider skips, plus managed frontend and isolated installation/recovery checks. Two independent Codex subagents completed three concurrent paired navigation rounds through separate real MCP connections: 0/6 independently scored successes, three false arrival claims, zero falls and 69 motion requests. All six failed complete-body target entry. Fixed spawn/goal geometry and scoring were unchanged.

Observed limitations include post-cancel hold oscillation, unconfirmed stopping after obstacle braking, and one empty cached capability manifest after a World failure that required operator refresh. In a separate Run-lifecycle check, B retained control after A ended, but its 0.5 m request stalled after 0.024 m. These findings limit the earlier development probes; the candidate is not navigation-qualified.

Actual protocol checks rejected cross-project access, cross-instance observation receipts and forged generation fields. B cancelled during A long polling in approximately 0.027 seconds. A transport loss revoked only A; reconnect preserved old receipts without executing them and rejected new motion without authorization. Shared World process loss failed both Runs. Ending A preserved both physical robots and B's running instance. These checks do not replace physical navigation success. Historical evidence remains separate from later candidates.
