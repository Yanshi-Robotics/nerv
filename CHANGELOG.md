# Changelog

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
