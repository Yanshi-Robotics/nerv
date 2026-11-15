# Connect an Agent to NERV

[中文](../i18n/zh/mcp.md) · [NERV](../README.md)

The 0.9.1 candidate exposes a local stdio MCP bridge to an already running Host. The bridge uses a paired credential and grants access only to selected projects. It does not launch another Host, connect motors, install resources or reveal model keys.

## Pair a client

1. Start the intended NERV channel and create a Project. The public installer does not install an unpublished candidate; obtain a candidate bundle through the development workflow first.
2. Open Settings → Agent connections. Enter a client name, select allowed projects and choose observation-only or control requests.
3. Create the connection and copy its client ID. NERV stores the bridge credential privately in that channel. An optional one-time token display is for integrations that need it; do not paste tokens into source files, issues or shared MCP configuration.
4. Configure the client's stdio MCP launcher with command `nerv` and these arguments:

```json
{
  "command": "nerv",
  "args": ["mcp", "--channel", "preview", "--brain", "BRAIN_ID", "--client", "CLIENT_ID"]
}
```

Replace `CLIENT_ID` with the paired ID. Use `stable` only when connecting to a stable Host that includes these APIs. The JSON is the launcher object; the surrounding configuration format depends on the client. If the desktop client cannot resolve `nerv`, use the installed launcher's absolute path. Do not put a token on the command line.

The corresponding terminal command is:

```bash
nerv mcp --channel preview --brain BRAIN_ID --client CLIENT_ID
```

The process waits for MCP protocol messages on stdio; it is not a chat shell. Client errors are written to stderr. If NERV restarts, reconnect the bridge, inspect current project/task state and obtain fresh observations before resuming work.

## Give control explicitly

Pairing and motion authorization are separate. Start the world or device workspace in NERV, select a compatible control scheme, prepare a real device through operator controls and authorize motion when appropriate. Open Manual controls, select the paired Agent and choose Accept Agent requests. Only one Agent owns motion control at a time.

Take manual control revokes external motion authority and requests a stop. A failed stop leaves control disabled; it does not confirm that the robot has stopped. Return control to Agent requires an explicit selected client. Pause Agent requests stops new requests without asserting that an existing execution ended. Stop task and emergency stop remain available separately.

Revoke a pairing from Settings to remove its access. Revocation, process restart and task cancellation must never be followed by replaying old action calls.

## Follow the observation loop

The exported tool names are the current contract, not examples of arbitrary command execution:

| Tools | Purpose |
|---|---|
| `projects_list`, `project_get`, `capabilities` | Discover allowed projects, their current run and declared actions |
| `run_start`, `run_end` | Start or end an owned simulation run; never connect real hardware |
| `observe`, `acknowledge` | Retrieve allowed sensor images and confirm the returned observation receipt |
| `task_start`, `task_status`, `task_update`, `task_cancel` | Work with stable tasks and their actual status |
| `action` | Request one allowed action inside an external task |
| `events`, `activity` | Read bounded events or publish an attributed short statement |

Start by reading the project and capabilities. Request observations, inspect all required images and metadata, then acknowledge their receipt. An acknowledgement records the client's assertion, not proof that a model understood the scene. Task starts and actions require fresh acknowledged observations, the current instance and control generation, and the operator's selected scheme.

`task_start` and `action` also accept an optional `receipt_id`. Supplying it explicitly confirms that the client has processed that delivered image; Brain validates the receipt and admits that request together. This avoids spending most of the motion-supervision interval on a model turn between separate acknowledgement and action calls. It does not acquire a new image, confirm an unread frame, refresh its capture time or increase the Runtime deadline. Use a new, actually processed receipt for each action. A receipt already confirmed separately or used by `task_start` cannot be confirmed again. Idempotent retries return the original result without confirming or executing again.

An in-flight action rejects additional acknowledgements. A later receipt cannot extend the old action's captured deadline. Cancellation bypasses the instance's acknowledgement/admission lock; an unconfirmed command termination blocks subsequent acknowledgement and task startup until stopping and execution termination are checked. Interrupted results distinguish execution uncertainty from a physical stop confirmation; they do not attribute every interruption to the operator.

Cancellation can leave the Runtime in a supervised hold while the connection still owns its previous control. `task_start` refuses held or unknown Runtime readiness before consuming its receipt or creating a task. An existing owner's `control_acquire` reports `runtime_held` and `reacquisition_required` instead of claiming readiness. Under an existing operator pregrant, explicitly call `control_release`, inspect a fresh observation, and call `control_acquire` for a new control generation; inspect another fresh observation before starting a new task. Recovery requires confirmed stopping and never resumes an old action. A held action reports `executed=false` and the measured `hold_reason` when available; a hold does not by itself identify an operator emergency stop.

`brain_get.world_health` reports the shared World's latest health classification. An unavailable probe disables motion and revokes the current control lease without ending an otherwise unconfirmed Run. After the same instance becomes healthy, obtain a new observation and reacquire control; no task or action resumes automatically. A confirmed process/epoch failure remains terminal for the old attachment. Image preparation is part of World readiness, and its diagnostic frames never acknowledge observations for a client.

`run_start` attaches execution records to existing resources. It cannot create a robot, start/restart a World or rebuild its scene. The operator must start the World first; a bootstrap allocation grant then allows the Agent to request its robot. A configured but unstarted World rejects allocation without consuming robot quota. After the allocation completes, attach the returned Brain, read its current state and start its Run. Ending that Run keeps the robot in the World.

Mutating requests require a caller-generated `request_id`. Reuse that ID only when retrying the identical request; changed arguments need a new request. Action calls also carry the current task ID and version. After a bounded action, observe again before the next action. Fixed ACT runs independently of model latency, but still requires the declared supervision cadence.

G1 simulation observations expose `state.image_frames[sensor]` for each returned PNG: the camera's `source_sequence`, actual `captured_sim_time`, capture wall time, World epoch, Robot/World IDs, topology revision and `image_sha256`. These values describe the image capture, not the earlier robot-state poll or later delivery. The Runtime `frame_seq` and observation receipt remain the acknowledgement mechanism; a camera source sequence is not an acknowledgement. Legacy sensors without capture metadata are explicitly marked unavailable for that evidence, rather than receiving an invented capture time.

G1 observations also expose robot-local sensor contracts. `state.proprioception` contains named joint positions/velocities, body-frame angular velocity and IMU specific force (including gravity), with individual validity and sample times. Missing or unmeasured values are unavailable, not zero. `heading_deg` is relative to the Runtime's initial heading; use it only while `heading_reference.valid` is true and `frame_id` remains unchanged. A reset invalidates the old reference.

`clearance_m` and `front_cone_m` are single-height point rays; a distant ray does not prove that the robot can pass. `motion_space.forward` and `.turn` expose the existing full-body braking preflight, its own sample time, measured body envelope and inhibit reason. These are body-local measurements, not a map, destination check or authorization. Execution checks again. A stopped or braked result describes the actual measured forward/lateral progress and turn; it does not prove that the task goal was reached.

Each image frame may include `calibration`: actual render resolution, perspective vertical FOV (degrees) or orthographic vertical span (metres), camera/base axis conventions and the transform from camera to the robot base at capture time. Only fixed cameras mounted on the controlled robot supply this transform; tracking or external cameras report unavailable calibration. Articulation can change it. Raw model intrinsic arrays declare their units and activation mode and are not a pixel intrinsic matrix. Sensor timestamps can differ; do not treat the observation as an atomic image/state sample. No World position, scene map or target coordinates are provided.

The development G1 simulation profile also declares `camera:ground`, a fixed downward camera on the robot's pelvis. It is a designed simulation sensor, not stock G1 hardware. The original head image and downward image retain self-occlusion and are captured sequentially, with their own sample times. Inspect both original images before confirming their receipt; neither a camera crossing a floor boundary nor seeing a peer inside a room establishes complete body entry.

An image's `own_robot_geometry` is computed under the same physics lock as its scene capture and carries that image's simulation time and calibration revision. It contains only this robot's articulated conservative collision bounds, a body-ground hull and declared-support hulls, expressed in that camera's frame (`+X` right, `+Y` up, `-Z` view). The projection plane uses the lowest declared support collision bound and gravity: it is not a measured sole, detected floor or contact plane. `pixel_projection.in_frame_projection` means only that projected coordinates fall within the image bounds; `occlusion_evaluated=false` means visibility has not been established. Contact and floor detection are also explicitly unevaluated. Missing support geometry or invalid transforms return unavailable data. The raw PNG is unchanged; these fields provide no room label, goal position, obstacle-clearance guarantee or arrival decision.

Use `events` with a bounded `wait_s` or poll `task_status` to inspect progress. Neither call renews observation supervision. When the model is delayed or disconnected, the local supervision deadline still applies. Do not acknowledge unseen frames to keep a task alive. A tool returning an execution result does not establish success without the task's independent success criterion.

## Client acceptance

| Client | Current statement |
|---|---|
| Codex 0.162.1 | Actual app-server initialized the bridge and discovered 14 tools; model conversation, image use and continuous supervision remain unverified |
| DeepSeek Harness | Intended stdio client; version-specific tool/image/task/stop acceptance pending |
| Claude Code | Intended stdio client; version-specific tool/image/task/stop acceptance pending |

Protocol fixtures and a local SDK client are useful software checks; they do not establish that a particular external application's model continuously supervises a task. Report the client name/version, NERV version, tools discovered, image handling, task/stop results and interruption behavior when contributing an acceptance result. Do not include private credentials or recordings.

## Scope

This release is same-machine only. The bridge rejects non-loopback Host receipts and detects Host instance changes. Browser operator APIs and Runtime node APIs are not general Agent interfaces. Remote authenticated transport and independent external Agent deployments are future work; see the [deployment diagram](../assets/readme/diagrams/en/deployments-091.svg).


## 0.9.1 resources and Brain binding

For a preauthorized simulation allocation, the operator creates an empty World in the console and grants a compatible template, one or more spawn slots, quota and optional control permission in Agent connections. Start `nerv mcp --channel preview --client CLIENT_ID` without `--brain` to discover resources. The bootstrap tools are `platform_describe`, `robot_allocate`, `operation_get`, `brain_attach` and `robot_remove`. Request IDs are scoped to the principal. Allocation/removal return a pending operation ID; poll `operation_get` until complete, failed or interrupted. Query uncertain outcomes instead of issuing another allocation. Host restart never replays an unfinished resource operation.

`brain_attach` changes the connection's tool list and fixes its Brain scope. A directly bound bridge can still use `--brain BRAIN_ID`. The bound tools include `brain_get`, `control_acquire` and `control_release`. Start a Run referencing the allocated Robot, observe, acquire control under the pregrant, observe again and acknowledge before beginning a task. Acquisition does not acknowledge an image. `task_update` remains retired; cancel and create a new task for a changed goal.

Robot and Brain survive Run completion. Authorized observers may coexist; only one owner controls motion. A reconnect restores reading, never motion. Control acquisition requires current observation and confirmed stopping. Heartbeats neither acknowledge observations nor extend an old action deadline. Resource allocation/removal requires an unbound connection and an operator World policy; Agents cannot reset or stop the entire World.

The previous reserved-Run flow remains a compatibility path. Candidate 23 completed installed shared-world engineering and three independently scored paired rendezvous rounds. The later Runtime metadata repair requires a new fixed installed candidate and fresh trials; the complete version remains under qualification. See the [development findings](brain-core-091.md).


After acquisition, check `ok` before any dependent request. A rejected acquisition must not be followed by `acknowledge`, `task_start` or `action`. Obtain a new observation to inspect the refusal; do not acknowledge an unseen image or recreate a task to bypass stopping. The bound owner may request ending its own standby simulation Run without motion ownership; the result still requires actual stop confirmation.

An operator can close further Agent allocation/removal requests with `POST /api/world-instances/{id}/agent-topology`, supplying `allow_agent_changes: false` and the current `expected_topology_revision`. This permission change waits for an in-flight membership transaction and rejects a stale revision. Existing robot entities, observations and control leases remain intact. It is not an Agent MCP tool; formal rendezvous trials close this gate after both instances are ready.

## Runtime adapter compatibility

The current 0.9.1 revision requires Runtime protocol 3.1 and Runtime SDK 0.8.3. The supported MCP Python baseline remains 1.30.0; no MCP wire-version upgrade is involved. Host-to-Runtime `tools/call` uses `_meta["nerv/control"] = {"token": "<runtime grant>"}`. Tool `arguments` contain only business input and retain the provider's original JSON Schema, including strict and nested schemas. Providers receive no control metadata. The legacy `_nerv_control` tool argument and dual-source credentials are refused rather than used as fallback. Control metadata coexists with progress notifications and is excluded from ordinary invocation and progress logs.

Runtime health advertises `control_transport: "mcp_request_meta"`; Host rejects an endpoint lacking this contract before preparation, including a cached attachment. Upgrade the adapter wrapper together with NERV. The bundled LeRobot adapter dependency revision is 0.8.4; this does not establish real-device qualification. External Agent tools and pairing credentials are unchanged.

After Host recovery, the previous successful World probe is retained as `last_known_health`, while current health is unknown until a fresh attachment/probe establishes it. Persisted timestamps never restore a live or movement-authorized state.
