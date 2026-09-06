# Architecture

NERV is the wiring between four kinds of node. It owns none of them.

<img src="images/structure.svg" alt="NERV structure" width="860">

| Node | Role | Process |
|---|---|---|
| Brain | System 2. Reasons once per step; answers the person; asks NERV to observe and to call tools | a plugin inside `nerv serve` (in-process today) |
| Body | System 1. Verbs (primitives and skills), policy loops at 30–50 Hz, the sensor summary | one process per body, in the interpreter its bus endpoint needs |
| World | physics, motor firmware, cameras, rays (simulation); reality (hardware) | one process per (world, body) in simulation; none for hardware |
| Tool | pure computation | one process per tool |

## Five interfaces, two planes each

| Interface | Between | Data plane (the brain may see) | Control plane (NERV and the operator only) |
|---|---|---|---|
| NERV/Operator | person ⇄ NERV | — | registry, sessions, chat + events, stop, arm |
| NERV/Brain | NERV ⇄ brain | `UserMessage`, `ToolResult` in; `Think`, `CallTool`, `SetRegister`, `Say` out | load, capabilities, usage |
| NERV/Body | NERV ⇄ body (MCP) | tools, `nerv://observation`, guidance, config, capabilities | `/health /status /config /stop /hold /release /stream` |
| NERV/World | body ⇄ world (bus) · NERV ⇄ world (sensors) | motor bus; sensor streams | launch, `/health` (epoch), spawn, `/reset`, `/status`, `/stream` |
| NERV/Tool | NERV ⇄ tool (MCP) | tools | `/health` |

The rule that keeps the whole thing honest: ground truth never crosses from the control plane to
the data plane. A simulated world knows where the robot is; the brain learns it the way a real
robot would — from the body's senses and from what its actions measured.

## One instruction, two clocks

<img src="images/flow.svg" alt="see, think, gate, act" width="860">

See: the brain asks to observe; NERV assembles one frame from the body's observation and any
ambient streams the session declared. Think: one model call — answer, or pick a tool. Gate: NERV
checks the session is armed, the verb is allowed and the arguments are inside what the body
declared; a refusal comes back as a tool result the brain can act on. Act: a primitive writes a
target and settles; a skill starts a policy loop on the body node that reads the bus, runs the
policy, writes joint targets and repeats until done, timed out or stopped, sending progress
heartbeats on the way and a measured result at the end. Then the brain sees again, until it
answers in words or one of three stops fires (interrupt > step ceiling > wall clock), each a
pause the person can resume.

## Session assembly

A session is brain × body × world (+ the tool nodes, + the ambient sensors it subscribes to).
`platform/registry/compat.py` decides whether the pair exists: the world must list the body in
`supports` (a sim world names the arena file) and the body must have a bus endpoint for the
world's kind. Then the launcher starts the world node, the body node (in the interpreter its
endpoint requires) and the tools, waits for each `/health`, records the world's epoch, and the
session starts disarmed. Trust is checked on the first handshake: an unapproved node contributes
no tools and no guidance until the operator approves its manifest.

## Where things live

```
src/nerv/nerve/       the five interfaces: message types, motor bus, sensors, conformance
src/nerv/platform/    registry, sessions, observation assembly, router, gate, launcher, trust, HTTP, CLI
src/nerv/brain/       the brain plugin: see–think–gate–act loop, model providers, prompts
src/nerv/body/        body node: MCP server, skill runner, families (humanoid, arm), bus endpoints
src/nerv/world/       world node: MuJoCo physics, motor firmware, cameras, rays, bus server
src/nerv/tool/        tool node: the calculator
bodies/ tools/        the registry: one directory, one YAML, one guidance.md per entry
worlds/               submodule nerv-world: scenes, robot models, arenas and the world descriptors
policies/             submodule nerv-policies: released policies (policy.onnx + contract.json + release.yaml)
```

## Future paths (not built)

- NERV/Tap: subscribe to every signal and record datasets (LeRobot format).
- NERV/Peer: several NERVs, several robots.
- ROS: a robot joins as a NERV/Body bridge, services as NERV/Tool bridges.
- Edge: NERV, the brain and the body on the robot's own computer; only NERV/World reaches hardware.
- Arena composition at runtime (MuJoCo `MjSpec.attach`) instead of prebuilt `supports` arenas.
- A retrieval tool for long-term knowledge; a grasp skill for the arm once a policy is trained.
