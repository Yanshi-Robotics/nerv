# NERV/Operator

The operator-facing surface: how a person (the web app, the CLI, or anything else) talks to
NERV. HTTP + Server-Sent Events, served by `nerv serve` (default `127.0.0.1:8000`). The brain
never sees any of this; everything here is control plane.

## Registry and brains

| Method · path | Returns |
|---|---|
| `GET /api/registry` | `bodies`, `worlds`, `tools` (registry entries without guidance), `matrix` (`[{world, body, ok, reason}]`), `brains`, `default_brain` |
| `GET /api/brains` | `[{name, vendor, label, model, hosting, available}]` |
| `GET /api/check?brain=` | `{ok, model, vision}` or `{ok:false, message}` |

## World Explore

| Method · path | Returns |
|---|---|
| `GET /api/worlds/{world}/explore` | The generated floor, room, facility and display-asset manifest |
| `GET /api/worlds/{world}/explore/assets/{filename}` | An allowlisted display asset with verified source and content hashes |

These read-only endpoints do not create sessions or launch world nodes. The display shows
the initial scene with daylight materials; it is not live simulation state. Missing resources,
changed sources or altered assets return an error. Paths cannot leave the registered asset
directory. The manifest, facility descriptions and task truth are never part of a brain's
observation or tool list.

## Nodes (launched or attached)

Node keys: `body:<body>`, `world:<world>/<body>`, `tool:<tool>`.

| Method · path | What |
|---|---|
| `GET /api/nodes` | every node: `key, kind, url, bus_url, alive, attached, log, meta`, plus `online`, `trust {state, reason, changes}`, `tools`, `family`, `sensors`; `local_simulation` identifies a platform-managed body in a simulated world |
| `GET /api/nodes/{key}/manifest` | the reviewable manifest (url, guidance, tools with kind/description/schema) and the trust decision |
| `POST /api/nodes/{key}/approve` | record the operator's approval of exactly this manifest → `{ok, hash}` |
| `POST /api/nodes/{key}/refresh` | drop the cached handshake; next call re-reads the node |
| `POST /api/nodes/{key}/config` `{key, value}` | forward a configuration change to the node's own `/config` |
| `GET /api/nodes/{key}/status` | the node's `/status` (for a world node this is ground truth — humans only) |
| `POST /api/nodes/{key}/stop-simulation` `{expected_world, expected_epoch, session_id?}` | stop a local body and its world → `{ok, stopped, frozen}`. Use a `body:<body>` key and the selected body's `meta.world` and `meta.epoch`. The session view also supplies its session ID. A changed instance, stale session, externally managed node or incomplete pair returns `400` without stopping nodes. |

A running body can be reused by a new session in the same world. To select a different world,
first stop its simulation through the node endpoint above, then create a new session.
Stopping interrupts affected sessions, disarms and freezes them, and stops the body before
its world. Their messages and saved observations remain available. This operation applies
only to a complete local simulation managed by this platform.

## Sessions

| Method · path | What |
|---|---|
| `POST /api/sessions` `{brain?, body?, world?, sensors:[], tools?}` | registry compatibility check → launch world node, body node, tool nodes → create. `400` with the reason when refused. Give body and world together, or neither (conversation only). |
| `GET /api/sessions` | summaries: `id, brain, body, world, status, created_at, title, sensors, tools, armed, epoch, core_task, notes` |
| `GET /api/sessions/{sid}` | summary + `messages` (roles `user`, `assistant` with `tool_calls`, `tool`, `perception` with `images:[{name, ref}]` and `state`, `brain_divider`) |
| `DELETE /api/sessions/{sid}` | delete the record and its images |
| `POST /api/sessions/{sid}/interrupt` | stop the running turn and ask the body node to stop its skill |
| `POST /api/sessions/{sid}/estop` | emergency stop that keeps the pose: interrupt the turn and `POST /hold` on the body node → `{ok, held, message}`. Written into the session as an operator line so the brain knows. Not a tool the brain can call. |
| `POST /api/sessions/{sid}/release` | lift the hold (refused while the body is down) |
| `POST /api/sessions/{sid}/reset` | cancel interactions, clear task progress, restore the entire simulated scene, then release the body controller; preserve the selected time of day |
| `POST /api/sessions/{sid}/brain` `{brain}` | switch brains mid-session |
| `POST /api/sessions/{sid}/arm` `{armed}` | the arming switch: sets the session flag and forwards to the body node's `/config`; arming is refused during scene testing |
| `GET /api/sessions/{sid}/tools` | the tool sheet the brain would see: `[{name, kind, origin (body · tool), node, description, parameters}]` — what the remote control renders |
| `POST /api/sessions/{sid}/teleop` `{name, arguments}` | the operator calls one tool directly. Same gate, same nodes, same log as the brain; SSE with `start · tool_call · gate · progress · tool_result · done`. The step is recorded in the session as taken by `operator`, so the brain sees it next turn. |

Session states: `active`, `frozen` (a newer session took the same body or its simulation was stopped), `reconnect_required`
(the world node's epoch changed — it restarted — so this session's physics is gone).
Inactive sessions cannot arm, stop, hold, release or reset a later body's simulation. Their
live views are disconnected; `perceive` returns empty state and images. Existing observations
remain in the conversation history.

## Scene testing

All paths below start with `/api/sessions/{sid}/scene`. They require an active session bound
to the current simulated world's epoch. A lease belongs to one session, browser owner and
world version. Operator controls apply bounded forces through the world's existing physics
loop; the robot continues to stand using its own controller.

| Method · suffix | Request and result |
|---|---|
| `GET /state` | Actual joint fractions, movable positions, selection, held body, phase and task progress |
| `GET /catalogue` | Source-generated facilities, labels, available operations and test instructions |
| `POST /acquire` | `{owner}` → state plus `owner, token, epoch, lease_seconds` |
| `POST /heartbeat` | `{owner, token, epoch}` → renewed lease and measured state |
| `POST /command` | credentials plus `sequence`, `action` and its arguments → measured state |
| `POST /release` | credentials → cancel interaction and relinquish the lease |
| `GET /frame?owner=&token=&epoch=` | One operator-camera JPEG, with `X-Sim-Time`; replaces the chase-camera display |

Acquiring a lease interrupts the automatic task, asks the body to stop, waits for stable
standing, and disarms the session. Robot motion and furniture control are mutually exclusive.
The user must arm the robot again after leaving testing. The configurable lease defaults to
two seconds; the web client renews every 500 milliseconds and has its own expiry watchdog.
Disconnecting, closing the page, freezing a session, switching worlds, resetting or falling
cancels interaction forces and restores temporary furniture damping.

Supported commands are `focus {facility}`, `view {lookat?, azimuth?, elevation?, distance?}`,
`select {xy}`, `joint {joint, fraction, xy}`, `grab {xy}`, `drag {xy, distance}`,
`release`, `cancel` and `time {phase}`. Image coordinates use normalized device coordinates:
`x` and `y` range from −1 to 1, with positive `y` upward. Selection raycasts the actual visible
surface and checks the two-metre reach again when opening a joint or grabbing an object.
Focusing a facility moves the camera; it does not select or operate that facility.

Scene command requests carry a strictly increasing positive `sequence` within the current lease. The world checks this number at execution time, including queued time-of-day updates. A completed cancellation rejects older requests that arrive late; heartbeats do not consume command numbers.

Task definitions and predicates belong to the world library. The refrigerator task checks
whole-object containment on the designated shelf, release, physical load-bearing contact,
sustained stability, door closure and the complete contact history. Its target volume appears
only in the operator camera. Mechanical knob states do not simulate cooking, temperature or
water flow. G1 manipulation and stair-climbing skills are separate work.

## Chat

`POST /api/chat {session, text}` → `{reply, stop_reason}`.
`POST /api/chat/stream {session, text}` → SSE, one JSON object per `data:` line:

| type | fields |
|---|---|
| `start` | `brain, model` |
| `perception` | `image_b64` (first image), `state`, `n_images`, `cameras` |
| `thinking` | `text` |
| `tool_call` | `name, args` |
| `gate` | `name, allowed, reason` |
| `progress` | `name, message, progress` (0–1) |
| `tool_result` | `name, ok, message` |
| `reply` | `text`, optional `stop_reason` ∈ `interrupt · steps · time` |
| `done` | — |

## Observation, images, signals

| Method · path | What |
|---|---|
| `GET /api/perceive?session=` | the observation the brain would get now: `{state, images:[{name, b64}]}` |
| `GET /api/imgfile?ref=` | a stored perception image |
| `GET /api/status` | version, node count, data root |
| `GET /api/config` | the four runtime parameters shown in the UI, and whether `NERV_TRUST_ALL` is on |
| `GET /api/nerv` | dashboard: nodes, sessions, brains, registry, recent signals |
| `GET /api/nerv/events?since=` | SSE of log entries (`llm_call`, `body_call`, `tool_call`, `world_call`, `gate`, `operator`) |
| `GET /api/session-logs?session=&limit=` | the session's log file, merged and ordered |

The CLI (`nerv chat`, `nerv run`, `nerv session`) uses the same hub in-process; the web app is a
static export served at `/` when `frontend/out` exists.
