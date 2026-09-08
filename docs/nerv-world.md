# NERV/World

What sits between a body and the world it stands in. In simulation the world node is a
process (MuJoCo) that owns physics, the motor firmware, the cameras and the rangefinder rays;
in reality there is no world node — the bus endpoint is the hardware driver.

## Data plane 1 — the motor bus (body ⇄ world)

Spoken by the body node at policy rate. Transport for simulation: ZMQ REQ clients and a ROUTER server, one JSON object
per message (`src/nerv/nerve/wire.py`); images base64. Shaped after Unitree LowCmd/LowState and
LeRobot `send_action`/`get_observation`.

| op | request fields | reply |
|---|---|---|
| `spawn` | `joint_names, pd_mode (implicit · explicit · position_actuator), kp, kd, torque_limit, default_pos, extra` | `{joint_names, joint_limits:[[lo,hi]], epoch, dt, has_free_base}` |
| `read` | — | `{t, joint_pos, joint_vel, imu_quat[w,x,y,z], imu_gyro, imu_acc, odom_xy, odom_yaw, base_height, extra}` |
| `write` | `targets, kp?, kd?` | `{ok}` — the world holds the latest targets and applies them every physics step |
| `reset` | — | reset the robot, furniture, props and task state; keep the selected visual phase |
| `sensors` | — | `{sensors:[...]}` streams mounted in the world (cameras, ambient) |
| `sensor` | `name` | `{mime, data (base64), t}` |
| `rays` | `angles_deg` (0 = ahead, +left), `max_range_m` | `{ranges}` |
| `clearance` | `motion`, horizon, sample spacing, height offsets and support parameters | `{valid, travel_clearance_m, support_drop_m, support_rise_m, turn_clear, reason}` |
| `epoch` · `close` | — | `{epoch}` · `{ok}` |

Units: radians, metres, seconds of simulation time. The PD loop lives on the world side of
this line in simulation for the same reason it lives in servo firmware in reality: `implicit`
puts `kd` into the joint damping and applies `kp·(q*−q)`; `explicit` applies
`kp·(q*−q) − kd·q̇` with model damping zeroed; `position_actuator` writes targets to the MJCF
position actuators. Getting the mode wrong makes a humanoid fall within a second, so it is a
property of the policy release (`release.yaml`), not a guess.

Motor and camera requests use independent client channels at the same address. A bounded
camera worker handles one image request at a time; extra requests receive a busy response.
Slow image generation does not hold the motor channel or delay emergency motor writes.

`clearance` reports body-relative distances against real collision geometry. It excludes
the entire robot and purely visual surfaces, including pool water. The request selects
`motion: forward | turn` and supplies `horizon_m`, `sample_spacing_m`, `height_spacing_m`,
`height_offsets_m`, `envelope_margin_m`, `support_body_names`, `max_drop_m`, `max_rise_m`,
`support_probe_margin_m` and `support_seam_tolerance_m`. The body supplies these from its
locomotion configuration. `travel_clearance_m` is the remaining travel beyond the measured
body envelope; the body applies its braking margin once. No room, object identity or world
coordinate is returned. Invalid measurements fail closed. An active scene-test lease returns
`valid: false, reason: scene_test_active`, including when the body is armed through its direct
node interface. These checks protect flat-ground skills; they do not add stair traversal.

## Data plane 2 — sensor streams (world → body policy, and → NERV)

A world may carry sensors of its own: an overhead camera over a desk, a room rangefinder.
They are streams named `<kind>:<name>` (`camera:top`). A body's policy subscribes over the bus
at full rate; NERV fetches one frame per step over `GET /sensors/<name>` and adds it to the
brain's observation when the session declared it. Sensors only: a JSON stream that carries
pose/room/truth keys is refused by the platform (`platform/observe.py`).

## Control plane — HTTP (NERV and the operator, never the brain)

| Method · path | What |
|---|---|
| `GET /health` | `{ok, node:"world", world, body, epoch, sim_time, realtime_factor}`; `epoch` changes when the process restarts — sessions bound to the old epoch become `reconnect_required` |
| `GET /status` | ground truth: base pose, yaw, tilt, fallen, room; for people and tests |
| `GET /sensors` · `GET /sensors/{name}` | stream list · one JPEG frame |
| `POST /reset` | cancel interactions, clear forces and task state, reset all simulation state; retain the selected time of day |
| `GET /stream` · `GET /stream/{camera}` | MJPEG chase camera (operator only) · a model camera |
| `GET /view` · `POST /view {zoom?, yaw?, pitch?}` | the operator's chase-camera nudge: `zoom` scales the distance, `yaw`/`pitch` (degrees) swing and tilt it around the body; clamped, eased over a few frames, and the stream is never reconnected. The web client resets it when the view opens and closes. |
| `GET /scene/state` · `GET /scene/catalogue` | actual interaction and task state · source-generated facilities and instructions |
| `POST /scene/acquire` | `{session, owner, epoch}` → short-lived operator lease, after standing stability checks |
| `POST /scene/heartbeat` · `POST /scene/release` | `{session, owner, token, epoch}` → renew or release the lease |
| `POST /scene/command` | lease credentials, `sequence`, and a selection, joint, drag, camera or time command |
| `GET /scene/frame` | lease credentials in the query → one operator-only JPEG |

Launch: `nerv node world -- --world apt --body humanoid-unitree-g1 --http-port P --bus-port Q`,
or let the platform do it when a session is created. Registry: `worlds/<name>/world.yaml`
(`kind`, `engine`, `assets_root`, `supports: {body: arena}`, `spawn`, `ambient`, `physics`).
`worlds/` is the `nerv-world` submodule: the scene library and the world
descriptors live together, and relative paths in a descriptor resolve against the nerv repository
root (`assets_root: worlds`).

Scene interaction is an optional world capability supplied by `scenes/operator.py` in the
registered asset root. Furniture control runs in the existing physics loop. Contact violations
are observed after every physics step; full task predicates update at a bounded lower rate.
Time changes run on the existing renderer thread so that material and texture updates reach
all cameras without resetting joint positions, object positions or velocities.

The platform owns session validation and leases. Facility identities, target regions, physical
task predicates and visual phases remain in the world library. None of the operator camera,
Explore metadata or task ground truth is a robot sensor. See [NERV/Operator](nerv-operator.md#scene-testing)
for the command shapes and lifecycle rules.

Scene command requests carry a strictly increasing positive `sequence` within the current lease. The world checks this number at execution time, including queued time-of-day updates. A completed cancellation rejects older requests that arrive late; heartbeats do not consume command numbers.

## Display resources

The optional `explore` field in `world.yaml` points to a generated manifest relative to
`assets_root`, for example `.cache/explore/apt/manifest.json`. Model nodes, floors, rooms,
facilities and routes are generated from the same scene and interaction definitions.
The exporter preserves compiled mesh transforms, texture coordinates and materials, then
converts source metres with Z up to glTF metres with Y up.

```bash
.venv/bin/python -m pip install -r worlds/requirements-explore.txt
.venv/bin/python worlds/tools/export_explore.py --scene apt
.venv/bin/python worlds/tools/export_explore.py --scene house
```

Prepare the [external furniture assets](../worlds/docs/interactions/README.md#asset-setup)
and regenerate the MuJoCo scenes first if their source has changed. Display GLBs contain
downloaded assets and remain outside Git; exporter source, metadata definitions and provenance
records are versioned. A display can be loaded without creating a simulation session.
