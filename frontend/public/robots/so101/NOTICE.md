# SO-101 web model

Source: https://github.com/TheRobotStudio/SO-ARM100, directory `Simulation/SO101`,
commit `7629d2ad9853d10fb903093a33ef6114099d97e5` (`so101_new_calib.urdf` and its 13 STL meshes).
The wrist camera mount and camera meshes (`so101_new_calib_camera.urdf`, `wrist_camera_mount_so101_v1.stl`,
`wrist_camera_so101_v1.stl`) are from commit `eecbe3e0a9ebb23e25ad7b2759b03884c6660903` of the same repository.
The Leader arm's three meshes (`wrist_roll_so101_v1.stl`, `handle_so101_v1.stl`, `trigger_so101_v1.stl`) and the
xacro that places them (`so101_ee_leader.xacro`) are from https://github.com/legalaspro/so101-ros-physical-ai,
commit `58318c905a2c61289fa907de85cb8473322fbe68`.
License: Apache-2.0 for both repositories (the same license text; see `LICENSE` in this directory).

Used by yanshirobotics.com's Robot Gallery as a browser model only.

## Modifications (required notice under Apache-2.0 §4b)

`so101.glb` is generated from the files above by `scripts/build-so101-glb.py`:

- meshes decimated to about a third of their triangles and exported with smooth shading, in one Draco-compressed GLB;
- the URDF joint tree rebuilt as glTF nodes (one origin node plus one pivot node per joint); joint limits are kept in a JSON sidecar;
- two named materials (`printed`, `motor`) replace the URDF colours so the page can recolour the printed parts;
- two Yanshi-designed accessories added as optional nodes: a clip-on wrist camera mount (placed at the official mount's origin) and
  an M2.5 screw version of the Waveshare control-board plate (same outline as the official plate);
- a second copy of the arm, the Leader, built from the same URDF with the Leader package's last two links (wrist-roll body,
  handle and trigger) in place of the fixed and moving jaw, placed 36 cm beside the Follower; every Leader node is prefixed `leader__`.

Joint axes, joint origins and link geometry are otherwise as upstream.

NERV 0.8 reuses this browser asset locally. The lightweight viewer excludes leader
nodes at display time and provides no robot control or live pose telemetry.
`view.json` records the gallery's display pose and camera direction. `poster.jpg`
is a local render of the follower in that pose, with the leader and optional
gallery accessories hidden. The image represents the standard model, not a
live device observation.
