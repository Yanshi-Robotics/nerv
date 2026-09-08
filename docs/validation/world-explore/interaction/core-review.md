# Core review of 4b12216

Date: 2026-09-08. Baseline: `4b12216f3b51a7087a3701cdfc0cd423e59c73d1`.
Review boundary: scene ownership, server entry points, arming ordering, WorldSim's
physics/render separation, cancellation and time changes. Production files were not
edited. The isolated reproducer uses transport doubles; no service, policy, physical
simulation or GPU was started. Existing 151-test and UI/physical acceptance results
were not rerun or represented as new evidence here.

## P1: node configuration bypasses scene ownership

The node configuration route in `src/nerv/platform/server.py:199-210` provides a second
arming path outside `Nerv.arm`. It has two independently reproducible gaps:

1. The route checks `inp.value.lower()` against `true/1/yes/on`. The actual body parser
   in `src/nerv/body/node.py:98-102` also strips whitespace and accepts `armed`.
   During an acquired scene lease, `armed`, ` true ` and `ARMED` therefore pass this
   route and set the body to armed. A plain `true` correctly returns 409. In the
   reproduced cases, the scene lease remains active while the body is armed and the
   session is disarmed.
2. Even a plain `true` can overtake acquisition. The route checks ownership before
   sending configuration and does not take `body_control_lock`. Delay that request
   before its remote write, acquire scene control normally, and then complete the
   old write. Acquisition's disarm finishes first; the old arm finishes afterward.
   The observed remote writes are `false`, then `true`, with the lease still active.

Evidence: `node-config-reproduction.json`. `reproduce_node_config.py` extracts the
exact baseline handler with its route decorator removed, then exercises the real
Nerv/SceneTests logic and BodyNode parser. Only the network replies/delay are doubled.
Its assertions confirm both defects; they do not assert that the defects are fixed.

The smallest correction is to serialize body-node configuration with the same
per-body lock used by `Nerv.arm` and acquisition, then check the lease while holding
that lock. Match the existing body's full arming-value semantics, including whitespace
and `armed`. Keep tool-node configuration on its current path and keep emergency
stop/lease interruption independent of this lock. Do not add a new permission model.

Current configuration scope was checked: both existing body families return no
family options and reject unknown keys (`families/humanoid.py:749-753`,
`families/arm.py:251-255`). There are no additional configuration keys that presently
send forces, change gains or command velocity. The node configuration endpoint
delegates to these implementations; it does not invoke movement tools.

Required focused regression cases are all accepted truthy spellings during a lease;
plain true delayed across acquisition; acquisition delayed behind a prior arming
write; disarming during a lease; tool-node options; and emergency stop remaining
independent of delayed configuration. Preserve the body's existing parser semantics.

## Other inspected flows

No additional delivery blocker was identified in the following inspected paths:

- SceneTests reserves ownership before slow entry work, rechecks reservation identity
  and active session state, and guards disarming with the per-body lock. Cancellation
  detaches local ownership before asynchronous remote release; late replies are tied
  to the exact earlier reservation. This prevents an old heartbeat/release from
  extending or removing a later owner (`scene_tests.py:42-153`).
- WorldSim applies commands under the model lock. `commit_command` checks both lease
  credentials and a strictly increasing safe integer sequence at execution time.
  Time jobs repeat that check on the render thread, so an older queued time change
  cannot execute after a newer cancellation (`mujoco_node.py:677-708`,
  `scene_operator.py:91-119`). Existing command-order tests exercise these races.
- The physical loop has one `mj_step` call site. It updates owned interaction forces
  before the step and observes continuous task predicates after every step. Time
  switching does not run a second clock (`mujoco_node.py:328-345`). Reset cancels the
  operator before resetting MuJoCo data, and retains the selected time phase.
- Native camera jobs prepare their scene under the model lock and render after
  releasing it. Operator, head and chase jobs use one context-owning render thread.
  Motor-bus sensor jobs have an independent bounded worker, while the ROUTER thread
  owns socket replies (`mujoco_node.py:663-767`, `world/bus_server.py:26-102`).
- Time changes run on the context-owning thread under model synchronization. The
  world library restores only visual model fields and refreshes derived camera/light
  values with `mj_camlight`; it does not reset or write joint positions/velocities.
  GPU texture upload duration, native appearance and sustained performance remain
  the separately measured runtime evidence; this review does not repeat them.

The generic node-configuration correction must land and receive its focused tests
before calling this reviewed baseline clear of the identified blocker. This report
does not extend the existing localhost control-plane threat model or certify arbitrary
future body plugins, device drivers or graphics failures.

## Working-tree fix verification — 2026-09-08

The root agent added the per-body lock around generic body configuration, with
accepted enable values normalized identically to BodyNode. The ownership check
is inside that lock. Independent execution of `test_scene_node_config.py` and
`test_scene_test_ownership.py` passed all 21 cases in 0.70 s. This includes the
seven enable aliases, configuration waiting behind scene entry, old enable IO
finishing before entry disarms, and emergency stop completing while disarm IO
holds the body lock. `stop`, `estop`, and `SceneTests.interrupt` still do not take
that lock. No new blocker was identified in this focused patch review.

These are CPU tests of the current working tree, not a commit or deployment claim.
The test process used isolated temporary sessions/logs and a nonexistent settings
file; it opened no service, browser, model policy or GPU context. The baseline
reproducer remains unchanged and intentionally targets the vulnerable baseline.
