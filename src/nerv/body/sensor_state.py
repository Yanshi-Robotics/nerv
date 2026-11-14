"""Robot-local sensor projection; no world pose, navigation map or inferred labels."""
from __future__ import annotations

import math
import hashlib


def proprioception(state, joint_names, *, source):
    stamp = state.t if type(state.t) in (int, float) and math.isfinite(state.t) else None

    def vector(values, length, unit, frame):
        valid = (stamp is not None and isinstance(values, (list, tuple)) and len(values) == length and length > 0
                 and all(type(v) in (int, float) and math.isfinite(v) for v in values))
        result = dict(valid=valid, sample_sim_time=stamp, unit=unit, frame=frame)
        if valid:
            result['values'] = list(values)
        else:
            result['reason'] = 'missing_or_invalid_sensor_sample'
        return result

    names = list(joint_names)
    joints = dict(names=names, position=vector(state.joint_pos, len(names), 'rad', 'named_robot_joints'),
                  velocity=vector(state.joint_vel, len(names), 'rad/s', 'named_robot_joints'),
                  layout_revision=hashlib.sha256('\0'.join(names).encode()).hexdigest())
    force = vector(state.imu_acc, 3, 'm/s^2', 'robot_base')
    if source == 'simulation_physics_derived':
        sample = state.extra.get('imu_specific_force_sample_sim_time')
        if type(sample) not in (int, float) or not math.isfinite(sample) or stamp is None or sample > stamp:
            force = dict(valid=False, sample_sim_time=None, unit='m/s^2', frame='robot_base',
                         reason='missing_or_invalid_sensor_sample')
        else:
            force['sample_sim_time'] = sample
    return dict(schema_revision=1, source=source, joints=joints,
                imu_angular_velocity=vector(state.imu_gyro, 3, 'rad/s', 'robot_base'),
                imu_specific_force=force)
