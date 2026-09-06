# NERV/Body

The contract between NERV and a body node. It is ANIMA Zero's AWI v1 with a new speaker:
four channels over MCP, and a control plane over plain HTTP that the brain never sees.

## Data plane — MCP at `/mcp/` (Streamable HTTP, stateless, SSE responses)

| Channel | MCP | Content |
|---|---|---|
| Tools | `tools/list`, `tools/call` | the body's verbs. `inputSchema` is an object schema; numeric arguments carry `minimum`/`maximum` and the gate enforces them. `readOnlyHint` marks `read` tools. Long calls send `notifications/progress` — a sign of life; the platform times out on silence, not on duration. Failure = `isError: true` with a message; structured data in `structuredContent`. |
| Observation | `resources/read nerv://observation` | first a text content: the state JSON; then zero or more `image/png` blobs. `state.cameras` lists the stream names in blob order; `state.armed` says whether the node is armed. Never fabricate a frame; a missing camera is reported in `state.sensor_errors`. |
| Guidance | `prompts/get guidance` | prose the body writes about itself: how to deal with it, never answers to the task |
| Config | `resources/read nerv://config` | `{options:[{key,label,description,value,choices}]}` — read-only here; changing it is a person's action over HTTP |
| Capabilities | `resources/read nerv://capabilities` | `{family, version, tools:{name:{kind}}, sensors, armed, epoch}` — kinds are `read`, `primitive`, `skill` |

## Control plane — HTTP

| Method · path | What |
|---|---|
| `GET /health` | `{ok, node:"body", body, family, version, world, bus, armed, held, epoch}` |
| `GET /status` | the family's diagnostic view (joint state, last action); for people |
| `GET /sensors` | `{sensors:[...]}` streams the body carries |
| `GET /config` · `POST /config {key, value}` | read / change options; `armed` is always one of them |
| `POST /stop` | stop the running skill |
| `POST /hold {reason?}` | **emergency stop that keeps the pose**: the family latches the targets it is tracking and stops thinking; mutating verbs are refused until released. Not a power cut — the joints stay commanded (a powered-off servo robot goes limp). A legged body is first told to stand and latched once still (`hold_settle_s`, `hold_still_rad_s`); a fallen body latches at once, and a fall latches the hold by itself (a gait policy fed a fallen body only thrashes). |
| `POST /release` | lift the hold; refused while the body is down (reset the world, or stand it up) |
| `GET /stream` · `GET /snapshot` | MJPEG / one JPEG of the first camera |

## Verbs: primitives and skills

A primitive writes one target and settles (`move_joints`, `nudge`, `set_gripper`). A skill starts
a learned behaviour that runs on its own until done, timed out or stopped, sending progress on
the way and a measured result at the end (`move_forward`, `turn_left`, `turn_right`). Both are
written once per family; the bus endpoint (simulation or hardware) is what differs.

| Family | Verbs | Observation state | Sensors |
|---|---|---|---|
| `humanoid` | skills `move_forward(meters)`, `turn_left(degrees)`, `turn_right(degrees)` | `clearance_m` (8 rays), `front_cone_m`, `fallen`, heading, `last_action` (measured) | `camera:head` |
| `arm` | read `read_joints`; primitives `move_joints(targets, duration_s)`, `nudge(joint, delta_deg)`, `set_gripper(percent)` | `joints_deg`, `gripper_percent`, `limits_deg`, `last_action` | `camera:wrist` (+ ambient streams the session subscribes to) |

## Arming

A body node starts disarmed when its bus endpoint is real hardware and armed when it is a
simulation. While disarmed, every `primitive` or `skill` call returns `ok: false, "the body is not
armed…"`. The platform holds the same switch per session and forwards it to the node
(`POST /api/sessions/{sid}/arm`), so hardware moves only when both agree.

## Trust

The body's guidance goes into the system prompt and its tool descriptions into the tool sheet,
so a body contributes nothing until the operator approved its manifest (SHA-256 of tools +
guidance, bound to the node's identity `body:<name>`, not its URL — a re-launch on another port stays approved). Changed manifest → asked again with a diff. `NERV_TRUST_ALL=1` is
the development escape hatch. `nerv conformance <url>` checks a node against this page.
