# SO-101 integration acceptance

Version 1.0.0 remains gated on physical tests. Software fixtures, a successful tensor forward pass and model registration do not satisfy this gate.

## Freeze the configuration

Choose one validated DeepSeek VLM and one validated OpenAI VLM. Freeze each model's endpoint, model ID, effort, request signature and output/timeout settings. Freeze the original ACT artifact hashes, processors, runtime protocol/adapter/LeRobot versions, calibration hash, camera configuration, robot binding epoch, starting pose and scene conditions.

Keep credentials private. Store only configuration references and signatures in evidence. Real-device preparation and all physical tests are performed by the operator.

## Ten trials

Run five framework integration trials with each selected model. For every trial retain session/event IDs, tool call and execution IDs, observations and their times, independent video, actual robot outcome, reported agent outcome, latency, supplier usage and billed or reconciled cost. If cost is unavailable, mark it unavailable rather than entering zero.

A successful physical task requires the complete original snack package inside the original box, released by the gripper, followed by the policy itself returning empty-handed to Rest. A separate return command cannot make a trial successful. One invocation may retry internally; the host must not restart it automatically to manufacture repeated attempts.

Each model must complete at least one task. All ten trials must handle execution and outcome correctly, with zero false success reports. An honest failure or uncertain result is allowed. Report actual success counts and timing; this finite integration test does not establish generalization or a model ranking.

## Fault tests

Check normal cancellation, late inference completion, late upstream requests, one-action exclusion, camera loss, supervisor loss and emergency stop. Verify that no old policy targets are dispatched after stop acknowledgement. A measured hold target is a separate stop operation, not continued policy execution.

Release the emergency stop and confirm disarmed standby, an invalidated previous task, no automatic replay, and a requirement for a fresh instruction and control authorization. Check preparation failures and restarting the host with an unfinished event log. Keep disconnect and torque release separate from ordinary cancellation.

## Record acceptance

The model registry accepts an operator evidence reference, request signature and prepared robot `binding_signature` through `POST /api/vlm/{id}/acceptance`. This records evidence status only; it never publishes a release or manufactures trial results. A changed request signature, calibration, camera configuration, runtime epoch or binding makes that record inapplicable.

Use `pending` until evidence exists, `failed` when a requirement is not met, and `passed` only after the applicable trial and fault requirements are reviewed. Final 1.0.0 publication remains a separate authorized action after both model records and the full integration evidence pass.
