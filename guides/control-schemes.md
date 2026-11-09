# Control schemes and execution contracts

[NERV](../README.md) · [Architecture](architecture.md) · [MCP](mcp.md) · [中文](../i18n/zh/control-schemes.md)

NERV 0.9.1 changes the control architecture. Brain Core is part of NERV and binds an external Agent to a Run and robot instance. It contains no decision model. A scheme is an operator-selected, immutable revision of allowed actions, task duration and action budget for a robot profile and environment.

## Supported execution routes

| Template | Decision owner | Execution |
|---|---|---|
| `external_vlm` | External Agent | Bounded primitive actions |
| `external_skills` | External Agent | Registered motion skills |
| `fixed_policy` | Operator selects one skill | Runtime executes the fixed policy |

Historical `policy_group`, `visual_direct` and managed model configurations remain readable but cannot start internal inference. Select and authorize a new external scheme explicitly. Existing model credentials are neither copied nor read. ACT and gait inference are Runtime execution functions, not an internal AI Agent.

`GET /api/control-schemes?sid=SESSION` reports the pinned revision and live compatibility. `POST /api/control-schemes` saves a revision. `POST /api/sessions/SESSION/control-scheme` selects it only after the previous task stops. `task_start` freezes the goal, scheme revision, policy fingerprints and instance. `task_update` is retired; cancel before starting a different task.

## Observations and authority

The operator allocates the Brain and Run, starts the simulation and explicitly assigns control. Starting a Run does not arm it. Each MCP connection addresses one Brain. A shared World contains separate Runtime instances; a profile name is not an instance identity.

The external Agent inspects the actual sensor images, acknowledges a fresh receipt, and supplies a bounded action. Receipts, task IDs and retries cannot transfer to another Brain. A completed action consumes its observation authorization: observe again before another action. Polling events or submitting an Agent statement does not renew supervision. Runtime enforces local stopping independently of model latency.

Control transfers revoke the previous generation before a confirmed stop. Restart or reconnection does not replay motion. A failed stop is reported as unconfirmed and blocks replacement execution. A single Run ending keeps its robot in the shared scene; only the operator can stop the whole World.

## Qualification and results

Different skill transitions are closed unless the adapter declares a qualified pair. The 0.9.1 G1 transition qualification and installed-candidate independent navigation acceptance are still pending. Development probes are not release qualification. No ACT skill transition or real-device validation is implied.

Action results distinguish measured displacement, target error, obstruction, fall, timeout and stable-stop confirmation. Crossing a requested travel threshold is not independent task success. `target_reached=null` means no qualified target tolerance has established arrival. An Agent statement is stored with its author, separately from Runtime results and operator-side scoring.

## Modules

`release.json` identifies modules, dependency locks and asset hashes. Installation creates and validates a new environment before registering it. Runtime dependencies stay outside the Host. Module installation, resource management, motion authorization and shared-world changes remain operator operations; MCP exposes no shell or installation tool.
