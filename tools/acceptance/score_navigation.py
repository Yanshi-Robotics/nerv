"""Operator-only conservative navigation scoring from frozen collision geometry.

Never expose these inputs or output to an acting Agent. A robot origin inside a
room is insufficient: its entire collision bounds and clearance must fit.
"""
from __future__ import annotations

import math


def geometry_verdict(robot, region, contract):
    low, high = robot['bounds']
    if not all(math.isfinite(v) for v in [*low, *high]):
        return {'inside': False, 'reason': 'invalid_geometry'}
    margin = contract['body_margin_m']
    x0, y0, x1, y1 = region['interior_xy']
    if not (x0 + margin <= low[0] <= high[0] <= x1 - margin
            and y0 + margin <= low[1] <= high[1] <= y1 - margin):
        return {'inside': False, 'reason': 'body_outside_region'}
    # The ground is removed when freezing obstacle geometry. Conservative AABBs
    # may reject a marginal pose, but never count intersection as standing space.
    for obstacle in region['obstacles']:
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
    last = {'inside': False, 'reason': 'no_samples'}
    for sample in samples:
        robot = sample['robots'][robot_id]
        fallen |= robot['status']['fallen']
        last = geometry_verdict(robot, contract['regions'][target], contract)
        quiet = max(map(abs, robot['joint_velocity']), default=0) <= contract['joint_velocity_max']
        stamp = robot['sim_time']
        if not last['inside'] or not quiet or (previous is not None and stamp - previous > contract['stable_seconds']):
            start = None
            stable = 0.0
        else:
            start = stamp if start is None else start
            stable = stamp - start
        previous = stamp
    success = bool(stop_confirmed and not fallen and stable >= contract['stable_seconds'])
    return {'success': success, 'fallen': fallen, 'stop_confirmed': stop_confirmed,
            'stable_seconds': stable, 'geometry': last, 'agent_claimed_success': claimed_success,
            'false_success_claim': bool(claimed_success and not success)}
