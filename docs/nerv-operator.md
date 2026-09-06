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

## Nodes (launched or attached)

Node keys: `body:<body>`, `world:<world>/<body>`, `tool:<tool>`.

| Method · path | What |
|---|---|
| `GET /api/nodes` | every node: `key, kind, url, bus_url, alive, attached, log, meta`, plus `online`, `trust {state, reason, changes}`, `tools`, `family`, `sensors` |
| `GET /api/nodes/{key}/manifest` | the reviewable manifest (url, guidance, tools with kind/description/schema) and the trust decision |
| `POST /api/nodes/{key}/approve` | record the operator's approval of exactly this manifest → `{ok, hash}` |
| `POST /api/nodes/{key}/refresh` | drop the cached handshake; next call re-reads the node |
| `POST /api/nodes/{key}/config` `{key, value}` | forward a configuration change to the node's own `/config` |
| `GET /api/nodes/{key}/status` | the node's `/status` (for a world node this is ground truth — humans only) |

## Sessions

| Method · path | What |
|---|---|
| `POST /api/sessions` `{brain?, body?, world?, sensors:[], tools?}` | registry compatibility check → launch world node, body node, tool nodes → create. `400` with the reason when refused. Give body and world together, or neither (conversation only). |
| `GET /api/sessions` | summaries: `id, brain, body, world, status, created_at, title, sensors, tools, armed, epoch, core_task, notes` |
| `GET /api/sessions/{sid}` | summary + `messages` (roles `user`, `assistant` with `tool_calls`, `tool`, `perception` with `images:[{name, ref}]` and `state`, `brain_divider`) |
| `DELETE /api/sessions/{sid}` | delete the record and its images |
| `POST /api/sessions/{sid}/interrupt` | stop the running turn and ask the body node to stop its skill |
| `POST /api/sessions/{sid}/brain` `{brain}` | switch brains mid-session |
| `POST /api/sessions/{sid}/arm` `{armed}` | the arming switch: sets the session flag and forwards to the body node's `/config` |
| `GET /api/sessions/{sid}/tools` | the tool sheet the brain would see: `[{name, kind, origin (body · tool), node, description, parameters}]` — what the remote control renders |
| `POST /api/sessions/{sid}/teleop` `{name, arguments}` | the operator calls one tool directly. Same gate, same nodes, same log as the brain; SSE with `start · tool_call · gate · progress · tool_result · done`. The step is recorded in the session as taken by `operator`, so the brain sees it next turn. |

Session states: `active`, `frozen` (a newer session took the same body), `reconnect_required`
(the world node's epoch changed — it restarted — so this session's physics is gone).

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
