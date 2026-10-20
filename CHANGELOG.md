# Changelog

Versions are managed from 0.2.0 on: every release gets an entry here and a `v<version>` tag.
The 0.1.x entries below are the pre-release trail.

## [Unreleased]

## [0.6.2] — development snapshot, 2026-10-21

- Split conversational Agent and System 2 into independent harness runs, context views,
  cancellation signals and usage scopes. Add versioned task updates and acknowledged stops.
- Separate five capability domains; runtime protocol 2 exposes robot actions independently
  from auxiliary Agent tools and sensors. Incompatible workers fail before preparation.
- Keep chat available during execution and teleoperation. Add independent task events and
  sourced robot-activity bubbles inside the camera frame, separate from raw image inputs.
- Add simulation-only World Control, per-instance MJCF generation, object schemas and source
  fingerprints. Reset keeps configuration; rebuild creates a new instance and freezes old sessions.
- Serialize journal readers with writers, preserve old histories, and mark interrupted tasks
  unknown without replay. Retain the G1 path and all prior README media.
- This local development snapshot does not certify paid VLM behavior, physical tasks or ultimate.

- Set the supported MuJoCo simulation baseline to 3.12, matching the qualified workbench environment.
- Enable SO-101 simulation with single-object basket placement, three-snack sorting and five-disk Hanoi worlds. Add replaceable rigid/native-flex objects, cameras, offline task measurements and asset qualification in the worlds submodule.
- Add validated `spawn.initial_joints` world configuration, retained during initialization and whole-scene reset. Keep task truth outside body observations and the real ACT binding independent.

## [0.6.1] — development snapshot, 2026-10-20

- Added provider-level, write-only API key settings for OpenAI, DeepSeek, Gemini, xAI
  and Anthropic, stored outside the checkout with private directory/file permissions.
  Existing environment keys are ignored by the visual registry; users re-enter keys.
- Added curated Gemini, Grok and Claude VLMs, native reasoning effort choices,
  stateless Interactions and Messages adapters, and signed continuation replay.
- Replaced JSON-only registration editing with model settings forms; invalidated stale
  readiness receipts when their request contract changes. Validation remains explicit
  and billable; offline software fixtures do not qualify models for physical acceptance.
- Runtime SDK and LeRobot adapter remain at 0.6.0; this host update does not change
  their protocol or the 1.0.0 physical acceptance requirements.

## [0.6.0] — development snapshot, 2026-10-19

- Added durable session journals, legacy migration, scoped history queries, whole-tool-group
  compaction, request budgeting, cancellable Responses providers and generation-guarded commits.
- Extracted independent runtime SDK and entry-point discovery; wrapped the existing simulator
  and added the LeRobot 0.6.1 native ACT adapter with shared cameras and local supervision.
- Registered the original snack_into_box artifact by file fingerprint. Added explicit device
  preparation, policy loading, managed execution and disarmed emergency-release behavior.
- Added visual-only model registration, write-only credentials, per-model effort and separate
  image/tool/continuation checks. Added startup robot/environment selection and named camera views.
- Context overflow now returns an explicit pause message; incomplete provider responses are
  retained in raw events without replaying their continuation as live tool calls.
- Physical acceptance, paid API verification and full rendered G1 regression remain separate.
  This development snapshot does not qualify for 1.0.0.

## [0.5.0] — 2026-10-19

Audited development baseline after moving the checkout. Recreated the local Python
entry points and editable installation at the current checkout. Existing v0.2.0
history and both submodule revisions are retained. Context, turn and gate baseline:
10 tests passed. This checkpoint does not certify hardware or current cloud APIs.

The 2026-09-08 interaction update adds operator testing for apt's 22 appliance joints
and nine movable objects, measured refrigerator-task evaluation, interruption and reset
handling, house lighting phases, and collision-based ground-support and turning checks.
Nerv World Explore provides a read-only floor guide built from the scene models. The
existing robot policy is unchanged; autonomous manipulation and stair traversal remain
outside this update. The development and validation records retain failures, actual routes,
browser measurements and the authorized local service activation.

The residence catalogue now exposes `apt` and `house`. The duplex keeps the original
apartment's Central Park environment and four lighting phases, and the hillside mansion
continues under the shorter name. Numbered maps are retired in the worlds submodule.

Starting a session refuses to reuse a live body connected to another world or world bus.
Externally attached bodies are checked against current health before reuse. Existing session
history is retained; updating the checkout does not restart or rebind resident nodes.

## [0.2.0] — 2026-09-06

First usable release. The framework is verified end to end on the Unitree G1 in the `apt2`
world: an OpenAI or Claude brain observes through the body's head camera, walks and turns with
the released gait policy, brakes at walls and reports measured distances; the operator arms the
session, drives the body from the remote control, holds the pose with E-STOP, resets the world,
and watches every signal on the dashboard and in the logs. The checkout is self-contained
(`worlds/` and `policies/` submodules, one virtualenv); trust is bound to node identity.
The SO-101 arm is registered with its LeRobot bus endpoint but has no world yet.

## [0.1.2] — 2026-09-06

Emergency stop that keeps the pose. `POST /hold` on a body node latches the targets the PD is
tracking and stops the family thinking; `POST /release` lifts it. A fall latches the hold by
itself, so a fallen humanoid no longer thrashes under its gait policy. A walking biped is first
told to stand and latched once still (`hold_settle_s`, `hold_still_rad_s`, measured on the
g1-29dof-turn gait) — freezing legs mid-stride topples it. `held` rides in `/health` and in the
brain's observation; mutating verbs are refused while held. Platform: `POST /api/sessions/{sid}/estop`,
`/release`, `/reset` (spawn pose, simulated worlds), each written into the session as an operator
line. Web: an E-STOP row in the remote control with Release and Reset world.

The chase camera can be nudged: `POST /view {zoom, yaw, pitch}` on the world node scales the
distance and swings / tilts the view around the body (clamped by `chase_zoom_*`, `chase_pitch_*`,
eased over a few frames by `chase_ease`) while the MJPEG stream stays connected; the web's
third-person tile carries zoom in/out, orbit left/right, tilt up/down and a default button, and
sends the default when the view opens and closes, so a reopened view is the default view. The
middle area no longer scrolls: the selected views share the height in a grid sized by their count.

Trust records are keyed by node identity (`body:<name>`, `tool:<name>`) and hashed over the
node's guidance and tools only, never its URL: the launcher hands out ports from a pool, and a
re-launched body used to come back as a stranger with its verbs hidden. Version-1 (URL-keyed)
trust files are dropped on load — approve each node once more. The remote control now says so
when the body node is unapproved or unreachable.

## [0.1.1] — 2026-09-03

Self-contained checkout. `worlds/` is now the `nerv-world` submodule (the scene library, carrying the
world descriptors) and `policies/` the `nerv-policies` submodule;
registry paths are relative to the repository root, so `ALICE_HOUSE_ROOT`, `NERV_POLICIES_ROOT` and
`NERV_SIM_PYTHON` are gone. One virtualenv (`pip install -e ".[all]"`) runs the platform, the world
node and the simulated bodies; `NERV_LEROBOT_PYTHON` stays optional for the real arm.

## [0.1.0] — 2026-09-02

First NERV. A platform (roscore without ROS) that couples a brain to a body in a world, with
tools within reach, over five interfaces — NERV/Operator, NERV/Brain, NERV/Body, NERV/World,
NERV/Tool — each split into a data plane (what the brain may see) and a control plane.

- Actions go to the body, not the world; the body and the world are two processes joined by a
  motor bus shaped after Unitree LowCmd/LowState and LeRobot send_action/get_observation.
- Two body families: `humanoid` (skills driven by a released gait policy) and `arm`
  (primitives). Two bodies: `humanoid-unitree-g1`, `arm-lerobot-so101`. One world: `apt2`
  (nerv-world, MuJoCo). One tool: `calculator`.
- A session is brain × body × world; the registry refuses pairs the world has no arena for.
  Sessions start disarmed; only the operator arms them, and the body node holds a second switch.
- Sensor streams: the body publishes what it carries, the world publishes what is mounted in
  it; NERV assembles the brain's observation and refuses anything that looks like ground truth.
- The brain is a plugin behind a message protocol: Claude, OpenAI-compatible, Ollama, mock.
