"""Validate an installed official sim module inside its own interpreter, on CPU."""
import json
import os
from pathlib import Path
import sys


VERIFY_STEPS = 20  # Short deterministic actuator response check, not a task trial.
VERIFY_CONTROL_FRACTION = 0.01  # One percent of the declared actuator range.


def check_engine():
    """A deterministic engine probe, independent of downloaded user scenes."""
    import mujoco
    import numpy as np
    model = mujoco.MjModel.from_xml_string('''<mujoco><option gravity="0 0 0"/>
      <worldbody><light pos="0 0 2"/><geom type="plane" size="2 2 .1"/>
        <camera name="probe" pos="0 -2 1" xyaxes="1 0 0 0 .4 1"/>
        <body pos="0 0 .3"><joint name="test" type="slide" axis="1 0 0" range="-1 1"/>
          <geom type="box" size=".1 .1 .1" rgba=".2 .5 .8 1"/></body>
      </worldbody><actuator><position joint="test" kp="10" ctrlrange="-1 1"/></actuator></mujoco>''')
    data = mujoco.MjData(model)
    data.ctrl[0] = VERIFY_CONTROL_FRACTION
    mujoco.mj_step(model, data, nstep=VERIFY_STEPS)
    if not np.isfinite(data.qpos).all() or data.qpos[0] <= 0:
        raise ValueError('Simulation engine actuator probe failed')
    with mujoco.Renderer(model, height=128, width=128) as renderer:
        renderer.update_scene(data, camera='probe')
        image = renderer.render()
        if image.shape != (128, 128, 3) or image.std() < 1:
            raise ValueError('Simulation engine rendering probe failed')
    return {'engine': 'mujoco', 'engine_version': mujoco.__version__, 'rendering': 'passed',
            'bounded_action_check': True, 'verified_worlds': [], 'task_success': None}


def check(root):
    root = Path(root)
    if sys.platform.startswith("linux"):
        os.environ.setdefault("MUJOCO_GL", "egl")
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        from nerv.world.rendering import configure_software_egl
        configure_software_egl()
    import mujoco
    import numpy as np
    if (root / 'module.json').is_file():
        from nerv.platform.registry.schema import BodySpec
        descriptor = json.loads((root / 'module.json').read_text())
        if descriptor.get('schema') != 2 or descriptor.get('engine') != 'mujoco' or descriptor.get('assets') != 'separate':
            raise ValueError('Unsupported simulation module descriptor')
        bodies = descriptor.get('bodies')
        if not isinstance(bodies, list) or not bodies or len(set(bodies)) != len(bodies):
            raise ValueError('Simulation module requires unique robot bindings')
        from nerv.asset_store import identifier
        for key in bodies:
            identifier(key)
            body = BodySpec.model_validate_json((root / 'bodies' / key / 'body.yaml').read_text())
            if body.name != key or set(body.buses) != {'sim'} or not body.buses['sim'].enabled:
                raise ValueError('Simulation module contains an invalid robot binding')
        return {**check_engine(), 'asset_installation': 'separate', 'bodies': bodies}
    from nerv.platform.registry import Registry
    registry = Registry([str(root)])
    results = []
    for world in registry.worlds.values():
        for arena in world.supports.values():
            model = mujoco.MjModel.from_xml_path(str(Path(world.assets_root) / arena))
            data = mujoco.MjData(model)
            mujoco.mj_forward(model, data)
            limited = np.flatnonzero(model.actuator_ctrllimited)
            if not len(limited):
                raise ValueError("Module has no bounded actuator")
            baseline = mujoco.MjData(model)
            actuator = int(limited[0])
            lower, upper = model.actuator_ctrlrange[actuator]
            data.ctrl[actuator] = np.clip(VERIFY_CONTROL_FRACTION * (upper - lower), lower, upper)
            for _ in range(VERIFY_STEPS):
                mujoco.mj_step(model, baseline)
                mujoco.mj_step(model, data)
            if np.allclose(data.qpos, baseline.qpos, rtol=0, atol=1e-10):
                raise ValueError("Simulation actuator produced no measured response")
            if not np.isfinite(data.qpos).all():
                raise ValueError("Simulation produced non-finite state")
            with mujoco.Renderer(model, height=480, width=640) as renderer:
                for camera in ("top", "wrist"):
                    renderer.update_scene(data, camera=camera)
                    image = renderer.render()
                    if image.shape != (480, 640, 3) or not image.any():
                        raise ValueError("Simulation camera verification failed")
            results.append(world.name)
    if not results:
        raise ValueError("Module contains no worlds")
    return {"verified_worlds": results, "bounded_action_check": True, "task_success": None}


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1]))))
