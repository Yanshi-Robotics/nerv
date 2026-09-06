# SO-101 MJCF fixture

Source: https://github.com/TheRobotStudio/SO-ARM100, directory `Simulation/SO101`,
commit `7629d2ad9853d10fb903093a33ef6114099d97e5` (copied 2026-09-02 from the local
read-only snapshot then under `Projects/Archive/official-repo/lerobot/`; that snapshot moved on
2026-09-06 into the historical archive folder under `运维与备份/`, and this fixture does not depend on it).
License: Apache-2.0 (see `LICENSE` in this directory).

Used by NERV only as a **test fixture** for the motor-bus tests (`tests/test_world_bus.py`):
a small position-actuated arm that loads in well under a second.

## Modifications (required notice under Apache-2.0 §4b)

The upstream MJCF carries no cameras, and NERV's sensor path needs at least one:

- `so101_new_calib.xml`: added `<camera name="wrist" …/>` inside the `gripper` body
  (matches the `camera: wrist` sensor declared in `bodies/arm-lerobot-so101/body.yaml`).
- `scene.xml`: added a fixed overhead `<camera name="top" …/>` in the world body.

Nothing else was changed; meshes and joint/actuator parameters are verbatim.
