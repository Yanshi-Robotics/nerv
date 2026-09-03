# NERV/World

What sits between a body and the world it stands in. In simulation the world node is a
process (MuJoCo) that owns physics, the motor firmware, the cameras and the rangefinder rays;
in reality there is no world node — the bus endpoint is the hardware driver.

## Data plane 1 — the motor bus (body ⇄ world)

Spoken by the body node at policy rate. Transport for simulation: ZMQ REQ/REP, one JSON object
per message (`src/nerv/nerve/wire.py`); images base64. Shaped after Unitree LowCmd/LowState and
LeRobot `send_action`/`get_observation`.

| op | request fields | reply |
|---|---|---|
| `spawn` | `joint_names, pd_mode (implicit · explicit · position_actuator), kp, kd, torque_limit, default_pos, extra` | `{joint_names, joint_limits:[[lo,hi]], epoch, dt, has_free_base}` |
| `read` | — | `{t, joint_pos, joint_vel, imu_quat[w,x,y,z], imu_gyro, imu_acc, odom_xy, odom_yaw, base_height, extra}` |
| `write` | `targets, kp?, kd?` | `{ok}` — the world holds the latest targets and applies them every physics step |
| `reset` | — | back to the spawn pose |
| `sensors` | — | `{sensors:[...]}` streams mounted in the world (cameras, ambient) |
| `sensor` | `name` | `{mime, data (base64), t}` |
| `rays` | `angles_deg` (0 = ahead, +left), `max_range_m` | `{ranges}` |
| `epoch` · `close` | — | `{epoch}` · `{ok}` |

Units: radians, metres, seconds of simulation time. The PD loop lives on the world side of
this line in simulation for the same reason it lives in servo firmware in reality: `implicit`
puts `kd` into the joint damping and applies `kp·(q*−q)`; `explicit` applies
`kp·(q*−q) − kd·q̇` with model damping zeroed; `position_actuator` writes targets to the MJCF
position actuators. Getting the mode wrong makes a humanoid fall within a second, so it is a
property of the policy release (`release.yaml`), not a guess.

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
| `POST /reset` | back to the spawn pose |
| `GET /stream` · `GET /stream/{camera}` | MJPEG chase camera (operator only) · a model camera |

Launch: `nerv node world -- --world apt2 --body humanoid-unitree-g1 --http-port P --bus-port Q`,
or let the platform do it when a session is created. Registry: `worlds/<name>/world.yaml`
(`kind`, `engine`, `assets_root`, `supports: {body: arena}`, `spawn`, `ambient`, `physics`).
