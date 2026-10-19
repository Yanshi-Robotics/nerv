# NERV architecture

NERV 0.6 separates task reasoning from device ownership. One instance holds one robot/environment binding; one active session has control. The frontend consumes registry capabilities instead of testing robot names.

The [accepted decision](decisions/0001-modular-runtime-harness.md) records the deployment and lifecycle choices.

## Boundaries

| Component | Owns | Cannot own |
|---|---|---|
| Host | sessions, control generations, arming, local node clients, logs | vendor robot objects, policy tensors |
| AgentHarness | model requests, tool decisions, supervision loop, cancellation | actuator access or permission changes |
| SessionMemory | durable raw events and session-scoped historical views | another session's data or live robot state |
| ContextManager / Compactor | request estimates, complete groups, source-linked summaries | deletion of raw history or robot tools |
| ModelProvider | wire protocol, streamed terminal response, usage, reasoning continuation | robot lifecycle |
| Runtime worker | device, sensors, local policy loop, dispatch authority | model credentials or other workers' devices |
| World worker | existing simulation, motor bus, operator scene controls | agent decisions |

The harness accepts a model, context manager and replaceable compactor. Storage and permissioned tools come from the host Link. `brain/plugin.py` is the application composition point; harness storage and budgeting do not import the platform or its configuration.

## Robot objects

`RobotProfile` identifies the model or variant and its semantic configuration. `EnvironmentBinding` identifies a sim or real connection, provider and environment settings. `RobotRuntime` implements lifecycle and current capabilities. `PolicyArtifact` records a format, files, fingerprints, input/output contract and operating conditions.

A skill connects those contracts to an implementation. It may use a learned policy or vendor controller. A tool is the skill interface shown to the model. Neither the SDK nor core translates every robot into LeRobot.

The reserved `BindingTransition` describes source, target and expected epoch only. No runtime switching endpoint exists. Calibration and evidence must not transfer from sim to real by matching names.

## Session data

`memory/sessions/<id>/events.jsonl` is authoritative. Events have contiguous sequence numbers, timestamps, kind and data. Every append is flushed and fsynced. File locks serialize allocation; a host lock serializes snapshot read/modify/write. Metadata snapshots derive task/notes and messages from the journal. A retained `.json.legacy` source allows an interrupted initial migration to resume.

Images live under the session's `imgs/` directory. Perception records contain relative references, capture state and observation/frame receipts. History tools have no session argument and return only the current session. Provider continuation is excluded from history search/read results.

Interrupted tools are represented as unknown outcomes when constructing a complete provider tool group; this does not assert that a robot action succeeded. Unfinished run events receive `run_interrupted` on startup. Nothing replays a tool call.

## Model context

The request estimate includes the system text and registers, summary, neutral history and provider items, tool schemas, an allowance per image and output reservation. Provider usage is stored independently. Models with known context capacities also bound the local request budget.

The context manager compacts only completed old groups before the newest user request. A separate tool-free model request sees the old text and previous summary. Commit requires a nonempty, smaller result, unchanged source fingerprint, enough remaining budget and a live request. Every summary records its source range, model, usage and journal time. Original events remain available.

## Control generations

Each incoming instruction invalidates the previous turn token. Durable text/register commits share the host's generation lock. A worker receives an immutable controller ID, monotonically increasing request generation and random token; late claims and late tool calls are refused. A new claim does not cancel a running ACT execution: the new agent turn must observe and reassess it.

For ACT, the command token is checked again immediately around execution startup after potentially slow model loading. The adapter then captures a dispatch generation; the final native `send_action` and generation revocation share a lock. Cancelling clears queued ACT work by ending that runner and resetting its action buffer. A fresh execution cannot start until the previous worker has exited and its measured hold has completed.

The simulation adapter forwards cancellation to its existing SkillRunner. Acquiring the runner and clearing its stop flag happen under the same command guard used by the SDK. Normal G1 stopping continues to use the existing skill's braking/settling behavior.

## Local supervision

ACT captures both cameras and motor positions from one LeRobot owner. Browser streams and agent observations read those camera sources, while policy inference and native actuator writes stay in the adapter process. Stale frames fail locally, including when inference outlives its observation freshness limit.

The host renews supervision after a current model response using the runtime epoch and an issued observation receipt. The local watchdog cancels an execution when supervision expires, without waiting for a cloud request. Compaction or a slow provider cannot keep motion authorized indefinitely.

Stopping acknowledges that the old policy generation cannot dispatch further targets; it does not claim instantaneous physical standstill. A measured pose hold is a separate native write. Emergency stop latches hold and disarms. Release returns disarmed standby and clears the old task; it never resumes it. Disconnect and torque release are separate adapter operations.

## Model registry

The private registry stores editable visual model declarations, per-model effort, selected model and interface checks. Keys have environment-over-private-store precedence. The frontend receives only credential status. Worker environments contain a small allowlist and explicitly declared artifact paths, never model API keys.

Readiness is tied to a hash of model configuration, effort and credential. Image/tool/continuation checks use no robot. Physical acceptance is separately tied to that request signature, the prepared robot binding fingerprint and a retained operator evidence record. The host refuses configuration changes during active turns or managed robot execution.

## Preserved systems

Worlds and policies remain unchanged submodules. Existing world scene ownership, epoch, operator controls, trust and truth-isolation checks remain in the host. Retained legacy brain providers and the explicit Mock software fixture are excluded from the production visual model list. Legacy body entrypoints remain for regression references; production launch uses the runtime SDK.
