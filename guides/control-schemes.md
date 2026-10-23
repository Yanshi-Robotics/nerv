# Control schemes and module contracts

[NERV](../README.md) · [Architecture](architecture.md)

A scheme is an operator-selected template bound to one robot and one environment. Its immutable revision contains a display name, route, decision model and effort (or none), allowed actions, time limit and action budget. It references registered skills; it contains neither credentials nor device paths nor policy tensors.

## Scheme and task API

`GET /api/control-schemes?sid=SESSION` returns saved latest revisions, the session's pinned revision, live action choices and readiness reasons. `POST /api/control-schemes` saves a new revision; editing an obsolete revision is refused. `POST /api/sessions/SESSION/control-scheme` chooses an ID/revision only when the previous task has stopped.

The three templates are `fixed_policy`, `policy_group` and `visual_direct`. Fixed policy permits one skill and no decision model. Policy group permits skills; visual direct permits primitives. Compatibility is checked against the live runtime capability manifest and the session's robot/environment. UI diagrams are generated from the same scheme fields.

The existing `/api/sessions/SESSION/robot-tasks` endpoint starts, inspects, updates or cancels tasks. Start freezes the scheme, model settings, policy fingerprints and instance identity. Fixed-policy launch never constructs a decision model. Model-based tasks have their own context and cancellation. Fixed tasks cannot be retargeted mid-execution.

Host filtering and Runtime protocol 3 grants both restrict actions. Expiry, cancellation, superseding generations and replaced instances reject late dispatch. The Agent can manage tasks but cannot directly invoke mutating robot capabilities. Operator test controls are separate, visible actions; they revoke old task control before claiming a bounded manual action.

Read-only status and wait operations remain available to the decision layer. A completed decision turn ends through the Host's scoped stop; operator cancellation uses the task interface. A runtime capability labelled `control` does not bypass the selected allowlist.

The current release supplies no verified automatic transitions between different ACT skills. A group may choose among its listed skills; switching to another skill within the same task stops for operator intervention. Extending this requires validated transition preconditions and physical evidence, not a prompt claiming that the next skill is safe.

## Feedback

An execution record, a model's completion judgment and independently verified task success are different states. Fixed tasks without a success evaluator report their execution state and `outcome=unknown`. Preserve requested targets, driver-returned targets and later measurements separately. Do not replace actual capture times with model request times.

## Official optional modules

`release.json` schema 1 lists product IDs, packages, extras, optional resources, dependency locks and asset SHA-256 hashes. Module installation uses a new environment, validates dependencies and runs its verification command before adding a runtime record. Installed simulation roots contain `bodies/`, `worlds/` and `assets/`; paths are resolved inside that environment. A real-device profile merges with the simulation binding instead of replacing it.

`GET /api/modules`, `POST /api/modules/MODULE/install` and `POST /api/modules/JOB/cancel` are operator endpoints. Jobs persist under the user home and survive browser refresh. They are not exposed as Agent shell tools. This release accepts only official module IDs and the official release origin. Arbitrary simulator ZIP or repository imports are intentionally not offered.

For developers, the existing registry and Runtime SDK remain the extension interface. A new simulator must supply versioned descriptors, robot capabilities, resource provenance/licenses, observation semantics, supported scenes and protocol compatibility. Validate it as a source plugin first. A package importing successfully does not establish rendering, action correctness or task performance.
