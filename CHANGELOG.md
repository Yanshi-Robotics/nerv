# Changelog

## [0.1.2] — 2026-09-06

Emergency stop that keeps the pose. `POST /hold` on a body node latches the targets the PD is
tracking and stops the family thinking; `POST /release` lifts it. A fall latches the hold by
itself, so a fallen humanoid no longer thrashes under its gait policy. A walking biped is first
told to stand and latched once still (`hold_settle_s`, `hold_still_rad_s`, measured on the
g1-29dof-turn gait) — freezing legs mid-stride topples it. `held` rides in `/health` and in the
brain's observation; mutating verbs are refused while held. Platform: `POST /api/sessions/{sid}/estop`,
`/release`, `/reset` (spawn pose, simulated worlds), each written into the session as an operator
line. Web: an E-STOP row in the remote control with Release and Reset world.

## [0.1.1] — 2026-09-03

Self-contained checkout. `worlds/` is now the `nerv-world` submodule (the scene library formerly
known as alice-house, carrying the world descriptors) and `policies/` the `nerv-policies` submodule;
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
  (alice-house, MuJoCo). One tool: `calculator`.
- A session is brain × body × world; the registry refuses pairs the world has no arena for.
  Sessions start disarmed; only the operator arms them, and the body node holds a second switch.
- Sensor streams: the body publishes what it carries, the world publishes what is mounted in
  it; NERV assembles the brain's observation and refuses anything that looks like ground truth.
- The brain is a plugin behind a message protocol: Claude, OpenAI-compatible, Ollama, mock.
