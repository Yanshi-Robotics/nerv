"""Image calibration expressed only relative to the controlled robot's base."""
import hashlib
import json

import mujoco
import numpy as np


def calibration(model, data, base_body, camera_id, width, height):
    if not 0 <= camera_id < model.ncam:
        return {'schema_revision':1, 'valid':False, 'reason':'invalid_camera_id'}
    if not 1 <= base_body < model.nbody or int(model.body_rootid[model.cam_bodyid[camera_id]]) != base_body:
        return {'schema_revision':1, 'valid':False, 'reason':'camera_not_on_controlled_robot'}
    if model.cam_mode[camera_id] != mujoco.mjtCamLight.mjCAMLIGHT_FIXED:
        return {'schema_revision':1, 'valid':False, 'reason':'camera_mode_not_robot_fixed'}
    base_rot = data.xmat[base_body].reshape(3, 3)
    transform = np.eye(4)
    transform[:3, :3] = base_rot.T @ data.cam_xmat[camera_id].reshape(3, 3)
    transform[:3, 3] = base_rot.T @ (data.cam_xpos[camera_id] - data.xpos[base_body])
    ortho = model.cam_projection[camera_id] == mujoco.mjtProjection.mjPROJ_ORTHOGRAPHIC
    optics = {'resolution_px':[int(width), int(height)],
              'projection':'orthographic' if ortho else 'perspective',
              ('vertical_span_m' if ortho else 'vertical_fov_deg'):float(model.cam_fovy[camera_id]),
              'model_intrinsic':model.cam_intrinsic[camera_id].tolist(),
              'model_sensor_size':model.cam_sensorsize[camera_id].tolist(),
              'intrinsics_mode':'sensor_size' if model.cam_sensorsize[camera_id, 0] > 0 else 'fovy',
              'model_intrinsic_semantics':{'order':['focal_x', 'focal_y', 'principal_x', 'principal_y'],
                  'unit':'length in the same units as model_sensor_size',
                  'active_when':'intrinsics_mode=sensor_size; otherwise use fovy',
                  'pixel_matrix':False},
              'model_resolution_px':model.cam_resolution[camera_id].tolist()}
    mount = {'position_m':model.cam_pos[camera_id].tolist(),
             'orientation_wxyz':model.cam_quat[camera_id].tolist()}
    revision = hashlib.sha256(json.dumps({'optics':optics, 'mount':mount},
                                        sort_keys=True, allow_nan=False).encode()).hexdigest()
    valid = bool(np.isfinite(transform).all())
    return {'schema_revision':1, 'valid':valid, 'calibration_revision':revision,
            'source':'simulation_model_and_robot_kinematics', 'sample_sim_time':float(data.time),
            **optics, 'camera_axes':{'right':'+X', 'up':'+Y', 'view':'-Z'},
            'base_axes':{'forward':'+X', 'left':'+Y', 'up':'+Z'},
            'mount':'robot_link; base transform varies with measured articulation',
            'transform_base_from_camera':transform.tolist() if valid else None}
