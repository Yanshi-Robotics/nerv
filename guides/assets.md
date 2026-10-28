# Import, edit and run simulation assets

[中文](../i18n/zh/assets.md) · [NERV](../README.md)

Open **Settings → My assets** to import an MJCF directory or ZIP, create a personal
copy, and save revisions. The source directory stays unchanged. Official packages
are immutable; create a personal copy before editing them.

## Save a personal version

1. Install an official package or import a directory containing `nerv-asset.json`.
2. Choose **Create personal copy** and expand **Files and revision** to find its folder.
3. Edit the MJCF, textures or manifest in that folder with your own editor.
4. Choose **Check and save revision**. File validation must pass before a revision is saved.
5. Choose **Check simulation loading** to load and step the scene in the installed
   simulation environment. Rendering quality and task success require separate checks.
6. Select the saved scene in **New Session**. A running session continues to use its
   original revision; saving changes does not replace its world.

Export creates a ZIP of the selected saved revision, including its manifest and license
files. Unsaved edits are not exported. The exported package can be imported into another
NERV profile without access to your original folder.

## Declare a scene

The [tabletop example](../examples/assets/tabletop/nerv-asset.json) demonstrates the file
format without a robot. A runnable scene also needs a compatible, installed simulation
binding. For example, a scene containing the standard SO-101 can declare:

```json
{
  "schema": 1,
  "id": "my-so101-scene",
  "name": "My SO-101 scene",
  "kind": "scene",
  "engine": "mujoco",
  "entry": "scene.xml",
  "license": "MIT",
  "runtime": {
    "bodies": ["arm-lerobot-so101"],
    "spawn": {"initial_joints": {}},
    "physics": {"dt": 0.002, "render_width": 640, "render_height": 480}
  }
}
```

Use the robot identifier registered by your installed Runtime and the actual license
of your files. Naming a robot does not add its geometry or make an incompatible
controller work. Include the robot model and all required meshes and textures in the
package. Joint names and initial positions must match that model.

NERV accepts declared data, not executable builders. Runtime declarations cannot choose
a Python interpreter, import modules, install dependencies or change real-device bindings.
Absolute paths, traversal, missing dependencies, symbolic links and executable plugins
are rejected. Native MuJoCo loading still requires compatible geometry and physics.

## Command line

```bash
nerv assets import --path /path/to/scene
nerv assets inspect --id <saved-id>
nerv assets clone --id <saved-id> --name "My copy"
nerv assets save --id <copy-id>
nerv assets check --id <copy-id>
nerv assets export --id <copy-id> --path /path/to/new-export.zip
```

The command and web interfaces use the same store and checks. Saved scene revisions
are addressed by their content fingerprint. Official updates do not merge or overwrite
personal copies. Runtime loading, controller compatibility and task performance are
separate checks; a load result does not certify a policy.

Archive an asset to hide it from new sessions. Saved versions, active sessions and personal
edits remain available for recovery; archiving does not reclaim disk space. Use the
Archived assets section to restore it. The equivalent commands are `nerv assets remove
--id <saved-id>` and `nerv assets restore --id <saved-id>`. Permanent asset deletion is not
provided in this release.
