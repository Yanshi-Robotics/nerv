"""Compile declared robot-mounted cameras without rewriting scene/vendor files."""
import re

import mujoco

from ..platform.registry.schema import SensorDecl


def apply_robot_cameras(spec, source_model, robot_root_body, declarations):
    """Validate ownership against the unmodified model before attaching any camera.

    Called before shared-instance namespacing. Cameras remain fixed on their own
    robot link; this path cannot add a world, tracking or chase camera.
    """
    pending = []
    names = set()
    if not 1 <= robot_root_body < source_model.nbody:
        raise ValueError('A camera overlay requires a controlled robot root')
    for declaration in declarations:
        sensor = SensorDecl.model_validate(declaration)
        mount = sensor.simulation_mount
        if mount is None:
            continue
        name = sensor.camera
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', name) or name in names:
            raise ValueError('Mounted camera names must be unique local identifiers')
        if mujoco.mj_name2id(source_model, mujoco.mjtObj.mjOBJ_CAMERA, name) >= 0:
            raise ValueError('Mounted camera cannot replace an existing camera')
        body = mujoco.mj_name2id(source_model, mujoco.mjtObj.mjOBJ_BODY, mount.link)
        if body <= 0 or int(source_model.body_rootid[body]) != robot_root_body:
            raise ValueError('Camera mount must belong to the controlled robot')
        if sensor.width <= 0 or sensor.height <= 0:
            raise ValueError('Mounted camera resolution must be positive')
        names.add(name)
        pending.append((sensor, mount))
    for sensor, mount in pending:
        spec.body(mount.link).add_camera(name=sensor.camera, pos=mount.position_m,
            xyaxes=mount.xyaxes, fovy=mount.vertical_fov_deg,
            resolution=[sensor.width, sensor.height],
            mode=mujoco.mjtCamLight.mjCAMLIGHT_FIXED,
            proj=mujoco.mjtProjection.mjPROJ_PERSPECTIVE)


def compile_robot_cameras(arena, declarations, root_name=None):
    """Single-robot equivalent of the shared World composition overlay."""
    model = mujoco.MjModel.from_xml_path(arena)
    if not declarations:
        return model
    from .mujoco_node import robot_root
    root, _ = robot_root(model, root_name)
    spec = mujoco.MjSpec.from_file(arena)
    apply_robot_cameras(spec, model, root, declarations)
    return spec.compile()
