"""Validate an installed official sim module inside its own interpreter, on CPU."""
import json
import os
from pathlib import Path
import sys


VERIFY_STEPS = 20  # Short deterministic actuator response check, not a task trial.
VERIFY_CONTROL_FRACTION = 0.01  # One percent of the declared actuator range.

def check(root):
    if sys.platform.startswith("linux"):
        os.environ.setdefault("MUJOCO_GL", "egl")
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        from nerv.world.rendering import configure_software_egl
        configure_software_egl()
    import mujoco
    import numpy as np
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
