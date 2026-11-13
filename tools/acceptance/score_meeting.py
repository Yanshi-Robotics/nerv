"""Independent paired rendezvous scoring; operator evidence never enters MCP."""
from __future__ import annotations
import math
from .score_navigation import _vector, _bounds, geometry_verdict


def score(record, robot_ids, contract, visual_reviews, stop_receipts, *, claimed_success):
    """Require complete physics evidence, a common terminal window and reviewed images.

    Thresholds are explicit frozen calibration inputs. No fallback treats missing
    speed, contacts, stop evidence or visual review as success.
    """
    if len(robot_ids) != 2 or len(set(robot_ids)) != 2:
        raise ValueError('Exactly two distinct participants are required')
    if contract['stable_seconds'] < 3 or not 0 < contract['max_sample_gap_s'] <= .1:
        raise ValueError('Meeting requires at least three seconds and gaps no larger than 0.1 seconds')
    if not 0 < contract['distance_min_m'] < contract['distance_max_m']:
        raise ValueError('Freeze a positive meeting distance interval')
    samples = record.get('samples', [])
    limits = record.get('execution_audit', {})
    elapsed = limits.get('navigation_wall_seconds')
    counts = limits.get('motion_requests', {})
    budget_valid = (_vector([elapsed]) and 0 <= elapsed <= 900
                    and all(type(counts.get(rid)) is int and 0 <= counts[rid] <= 60 for rid in robot_ids)
                    and limits.get('source') == 'mcp_receipts_and_run_task_journal'
                    and limits.get('complete') is True)
    valid = (budget_valid and record.get('physics_events_complete') is True and record.get('dropped_samples') == 0
             and record.get('finished') is True and bool(samples) and not record.get('error'))
    start = previous = identity = None
    stable = 0.
    anchors = {}
    collision = record.get('robot_collision_seen') is not False
    fallen = record.get('fall_seen') is not False
    reason = 'no_samples'
    for index, sample in enumerate(samples):
        robots = [sample.get('robots', {}).get(rid, {}) for rid in robot_ids]
        stamp = sample.get('sim_time')
        current_identity = (sample.get('world_instance_id'), sample.get('epoch'), sample.get('topology_revision'))
        complete = (_vector([stamp]) and all(current_identity) and type(sample.get('robot_collision_seen')) is bool
                    and type(sample.get('fall_seen')) is bool and sample.get('sequence') == index + 1
                    and type(sample.get('physics_steps')) is int and sample['physics_steps'] > 0)
        for rid, r in zip(robot_ids, robots):
            complete &= (_bounds(r.get('bounds')) and _vector(r.get('joint_velocity'))
                and _vector(r.get('base_velocity'), 6) and r.get('robot_instance_id') == rid
                and r.get('world_instance_id') == current_identity[0] and r.get('epoch') == current_identity[1]
                and r.get('sim_time') == stamp and type(r.get('status',{}).get('fallen')) is bool)
        complete = complete and (previous is None or stamp > previous) and (identity is None or identity == current_identity)
        if not complete:
            valid = False
            start, stable = None, 0.
            reason = 'invalid_or_discontinuous_evidence'
            continue
        collision |= sample['robot_collision_seen']
        fallen |= sample['fall_seen'] or any(r['status']['fallen'] for r in robots)
        identity = current_identity
        positions = [[(r['bounds'][0][j] + r['bounds'][1][j])/2 for j in range(2)] for r in robots]
        distance = math.dist(*positions)
        quiet = all(max(map(abs,r['joint_velocity'])) <= contract['joint_velocity_max']
                    and math.hypot(*r['base_velocity'][:3]) <= contract['base_linear_velocity_max']
                    and math.hypot(*r['base_velocity'][3:]) <= contract['base_angular_velocity_max'] for r in robots)
        inside = all(geometry_verdict(r,contract['region'],contract)['inside'] for r in robots)
        eligible = quiet and inside and contract['distance_min_m'] <= distance <= contract['distance_max_m']
        gap = previous is not None and stamp - previous > contract['max_sample_gap_s'] + 1e-9
        drift = start is not None and any(math.dist(anchors[rid],pos) > contract['window_drift_max_m'] for rid,pos in zip(robot_ids,positions))
        if not eligible or gap or drift:
            start, stable, anchors = None, 0., {}
            reason = 'unstable_geometry_distance_drift_or_gap'
        else:
            if start is None:
                start, anchors = stamp, dict(zip(robot_ids, positions))
            stable = stamp - start
            reason = 'common_stable_window'
        previous = stamp
    # The contract requires a common window of AT LEAST three seconds. Keep
    # its full uninterrupted extent: visual reasoning can take longer than three
    # seconds while both robots remain stationary. Never cross a reset or gap.
    window = [start, previous] if start is not None else None
    images_valid = stops_valid = bool(window)
    for rid in robot_ids:
        reviewed = visual_reviews.get(rid, [])
        selected = [r for r in reviewed if window and r.get('seen_other_reviewed') is True
                    and r.get('agent_claimed_seen_other') is True
                    and r.get('robot_instance_id') == rid and r.get('world_epoch') == (identity or (None,None))[1]
                    and _vector([r.get('sim_time')]) and window[0] <= r['sim_time'] <= window[1]
                    and isinstance(r.get('image_sha256'), str) and len(r['image_sha256']) == 64
                    and all(c in '0123456789abcdef' for c in r['image_sha256'])]
        acquisitions = {}
        consistent = True
        for frame in selected:
            sequence = frame.get('observation_sequence')
            if type(sequence) is not int or sequence < 0:
                consistent = False
                continue
            acquisition = (frame['sim_time'], frame['image_sha256'])
            if sequence in acquisitions and acquisitions[sequence] != acquisition:
                consistent = False
            acquisitions[sequence] = acquisition
        images_valid &= (consistent and len(acquisitions) >= 3
                         and len({value[0] for value in acquisitions.values()}) >= 3)
        # Equal image bytes are legitimate for an unchanged scene. Independent
        # acquisitions require distinct sequence numbers AND acquisition times;
        # content hashes identify retained originals, not physical movement.
        stop = stop_receipts.get(rid, {})
        stops_valid &= (stop.get('stop_confirmed') is True and _vector([stop.get('observed_sim_time')])
                        and stop.get('robot_instance_id') == rid and stop.get('world_epoch') == (identity or (None,None))[1]
                        and window is not None and window[1] - contract['max_sample_gap_s'] <= stop['observed_sim_time'] <= window[1])
    success = bool(valid and not collision and not fallen and stable + 1e-9 >= contract['stable_seconds']
                   and images_valid and stops_valid)
    return dict(success=success, evidence_valid=valid, budget_valid=budget_valid, common_stable_seconds=stable, stable_window=window,
                visual_evidence_valid=bool(images_valid), stops_confirmed=bool(stops_valid),
                robot_collision=collision, fallen=fallen, reason=reason,
                agent_claimed_success=bool(claimed_success), false_success_claim=bool(claimed_success and not success))
