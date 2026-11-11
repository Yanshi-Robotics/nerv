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

Mutating requests require a caller-generated `request_id`. Reuse that ID only when retrying the identical request; changed arguments need a new request. Action calls also carry the current task ID and version. After a bounded action, observe again before the next action. Fixed ACT runs independently of model latency, but still requires the declared supervision cadence.

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

For a preauthorized simulation allocation, the operator creates an empty World in the console and grants a compatible template, one or more spawn slots, quota and optional control permission in Agent connections. Start `nerv mcp --channel preview --client CLIENT_ID` without `--brain` to discover resources. The bootstrap tools are `platform_describe`, `robot_allocate`, `operation_get`, `brain_attach` and `robot_remove`. Request IDs are scoped to the principal; query uncertain outcomes instead of issuing another allocation.

`brain_attach` changes the connection's tool list and fixes its Brain scope. A directly bound bridge can still use `--brain BRAIN_ID`. The bound tools include `brain_get`, `control_acquire` and `control_release`. Start a Run referencing the allocated Robot, observe, acquire control under the pregrant, observe again and acknowledge before beginning a task. Acquisition does not acknowledge an image. `task_update` remains retired; cancel and create a new task for a changed goal.

Robot and Brain survive Run completion. Authorized observers may coexist; only one owner controls motion. A reconnect restores reading, never motion. Control acquisition requires current observation and confirmed stopping. Heartbeats neither acknowledge observations nor extend an old action deadline. Resource allocation/removal requires an unbound connection and an operator World policy; Agents cannot reset or stop the entire World.

The previous reserved-Run flow remains a compatibility path. Current dynamic lifecycle code is under qualification; long-duration stable holding and installed-candidate rendezvous acceptance remain incomplete. See the [development findings](brain-core-091.md).
