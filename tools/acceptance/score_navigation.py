"""Operator-only conservative navigation scoring from frozen collision geometry.

Never expose these inputs or output to an acting Agent. A robot origin inside a
room is insufficient: its entire collision bounds and clearance must fit.
"""
from __future__ import annotations

import math


def _vector(value, size=None):
    return (isinstance(value, (list, tuple)) and bool(value)
            and (size is None or len(value) == size)
            and all(type(v) in (int, float) and math.isfinite(v) for v in value))


def _bounds(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(_vector(v, 3) for v in value)
            and all(a <= b for a, b in zip(*value)))


def geometry_verdict(robot, region, contract):
    if not _bounds(robot.get('bounds')):
        return {'inside': False, 'reason': 'invalid_geometry'}
    low, high = robot['bounds']
    margin = contract['body_margin_m']
    x0, y0, x1, y1 = region['interior_xy']
    if not (x0 + margin <= low[0] <= high[0] <= x1 - margin
            and y0 + margin <= low[1] <= high[1] <= y1 - margin):
        return {'inside': False, 'reason': 'body_outside_region'}
    if 'floor_z' in region and abs(low[2] - region['floor_z']) > contract['floor_tolerance_m']:
        return {'inside': False, 'reason': 'wrong_floor_or_unsupported'}
    # The ground is removed when freezing obstacle geometry. Conservative AABBs
    # may reject a marginal pose, but never count intersection as standing space.
    for obstacle in region['obstacles']:
        if not _bounds(obstacle.get('bounds')):
            return {'inside': False, 'reason': 'invalid_obstacle_geometry'}
        a, b = obstacle['bounds']
        if all(low[i] - margin < b[i] and high[i] + margin > a[i] for i in range(3)):
            return {'inside': False, 'reason': 'obstacle_clearance', 'obstacle': obstacle['name']}
    if robot['status']['fallen']:
        return {'inside': False, 'reason': 'fallen'}
    return {'inside': True, 'reason': 'whole_body_in_clear_region'}


def score(samples, robot_id, target, contract, *, stop_confirmed, claimed_success):
    """Score retained samples; a discontinuity resets the stable interval."""
    start = None
    previous = None
    stable = 0.0
    fallen = False
    invalid = False
    epoch = None
    last = {'inside': False, 'reason': 'no_samples'}
    for sample in samples:
        robot = sample.get('robots', {}).get(robot_id, {})
        stamp = robot.get('sim_time')
        velocities = robot.get('joint_velocity')
        base = robot.get('base_velocity')
        current_epoch = robot.get('epoch')
        valid = (_vector([stamp]) and _vector(velocities) and _vector(base, 6)
                 and isinstance(current_epoch, str) and bool(current_epoch)
                 and type(robot.get('status', {}).get('fallen')) is bool)
        valid = valid and (previous is None or stamp > previous) and (epoch is None or epoch == current_epoch)
        if not valid:
            invalid = True
            start, stable = None, 0.0
            last = {'inside': False, 'reason': 'invalid_or_discontinuous_evidence'}
            continue
        epoch = current_epoch
        fallen |= robot['status']['fallen']
        last = geometry_verdict(robot, contract['regions'][target], contract)
        quiet = (max(map(abs, velocities)) <= contract['joint_velocity_max']
                 and math.hypot(*base[:3]) <= contract['base_linear_velocity_max']
                 and math.hypot(*base[3:]) <= contract['base_angular_velocity_max'])
        if not last['inside'] or not quiet or (previous is not None and stamp - previous > contract['max_sample_gap_s']):
            start = None
            stable = 0.0
        else:
            start = stamp if start is None else start
            stable = stamp - start
        previous = stamp
    success = bool(stop_confirmed and not invalid and not fallen and stable >= contract['stable_seconds'])
    return {'success': success, 'fallen': fallen, 'stop_confirmed': stop_confirmed,
            'evidence_valid': not invalid,
            'stable_seconds': stable, 'geometry': last, 'agent_claimed_success': claimed_success,
            'false_success_claim': bool(claimed_success and not success)}
