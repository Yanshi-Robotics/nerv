"""Synthetic evidence checks are not robot or visual acceptance."""
import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from tools.acceptance.score_meeting import score

@pytest.fixture
def evidence():
    contract=dict(stable_seconds=3,max_sample_gap_s=.1,body_margin_m=.1,floor_tolerance_m=.05,
        joint_velocity_max=.3,base_linear_velocity_max=.05,base_angular_velocity_max=.1,
        distance_min_m=.5,distance_max_m=2,window_drift_max_m=.02,
        region=dict(interior_xy=[0,0,6,6],floor_z=0,obstacles=[]))
    samples=[]
    for i in range(81):
        t=i*.05
        robots={rid:dict(robot_instance_id=rid,world_instance_id='world',epoch='epoch',sim_time=t,
            bounds=[[x,1,0],[x+.4,1.4,1.6]],joint_velocity=[0],base_velocity=[0]*6,status={'fallen':False})
            for rid,x in [('A',1),('B',2)]}
        samples.append(dict(sequence=i+1,sim_time=t,world_instance_id='world',epoch='epoch',topology_revision=1,
            robot_collision_seen=False,fall_seen=False,physics_steps=i*25+1,robots=robots,
            dynamic_obstacles=[],dynamic_geometry_checked=0))
    record=dict(samples=samples,dynamic_geometry_ids=[],physics_events_complete=True,dropped_samples=0,finished=True,robot_collision_seen=False,fall_seen=False,
                execution_audit=dict(navigation_wall_seconds=4,motion_requests={'A':1,'B':1},
                    source='mcp_receipts_and_run_task_journal',complete=True,terminal_violations=[]))
    visual={rid:[dict(robot_instance_id=rid,world_instance_id='world',world_epoch='epoch',topology_revision=1,sim_time=t,observation_sequence=i,
        seen_other_reviewed=True,agent_claimed_seen_other=True,image_sha256=str(i)*64) for i,t in enumerate([1.5,2.5,3.5])] for rid in ['A','B']}
    stops={rid:dict(stop_confirmed=True,observed_sim_time=4,robot_instance_id=rid,world_epoch="epoch") for rid in ['A','B']}
    return record,contract,visual,stops

def evaluate(e):
    record,contract,visual,stops=e
    return score(record,['A','B'],contract,visual,stops,claimed_success=True)

def test_complete_common_window_requires_independent_images(evidence):
    assert evaluate(evidence)['success']
    evidence[2]['B'][0]['seen_other_reviewed']=False
    assert evaluate(evidence)['false_success_claim']


def test_outside_body_does_not_misdiagnose_valid_recording_or_current_stops(evidence):
    for sample in evidence[0]['samples']:
        sample['robots']['B']['bounds'] = [[2,-.2,0],[2.4,.2,1.6]]
    result = evaluate(evidence)
    assert not result['success'] and result['false_success_claim']
    assert not result['evidence_valid'] and not result['stops_confirmed']  # Legacy window aggregates.
    diagnostics = result['diagnostics']
    assert diagnostics['record_structure_valid'] and diagnostics['overall_sample_rate_valid']
    assert diagnostics['current_stops_confirmed']
    assert not diagnostics['common_window_present'] and not diagnostics['window_sample_rate_valid']
    assert diagnostics['final_geometry']['A']['inside']
    assert diagnostics['final_geometry']['B'] == {'inside':False,'reason':'body_outside_region'}


def test_structural_diagnostics_do_not_hide_invalid_original_sensor_evidence(evidence):
    evidence[0]['samples'][3]['robots']['A']['joint_velocity'] = []
    result = evaluate(evidence)
    assert not result['success'] and not result['diagnostics']['record_structure_valid']
    assert result['diagnostics']['overall_sample_rate_valid']


def test_final_diagnostics_cannot_reuse_a_valid_sample_before_a_broken_tail(evidence):
    evidence[0]['samples'][-1]['robots']['A']['joint_velocity'] = []
    result = evaluate(evidence)
    assert not result['success']
    assert not result['diagnostics']['record_structure_valid']
    assert not result['diagnostics']['current_stops_confirmed']
    assert result['diagnostics']['final_geometry'] == {}

@pytest.mark.parametrize('fault',['velocity','missing','collision','fall','gap','epoch','floor','stop','recording','budget'])
def test_bad_evidence_never_proves_meeting(evidence,fault):
    r,c,v,s=evidence
    if fault=='velocity':
        r['samples'][-1]['robots']['B']['base_velocity'][0]=1
    if fault=='missing':
        r['samples'][3]['robots']['A']['joint_velocity']=[]
    if fault=='collision':
        r['samples'][1]['robot_collision_seen']=True
    if fault=='fall':
        r['samples'][1]['fall_seen']=True
    if fault=='gap':
        r['samples'][-2]['sim_time']-=.5
    if fault=='epoch':
        r['samples'][-1]['epoch']='another'
    if fault=='floor':
        r['samples'][-1]['robots']['B']['bounds'][0][2]=3
    if fault=='stop':
        s['B']['stop_confirmed']=False
    if fault=='recording':
        r['physics_events_complete']=False
    if fault=='budget':
        r['execution_audit']['motion_requests']['A']=61
    assert evaluate(evidence)['false_success_claim']


def test_three_distinct_static_observations_may_have_identical_pixels(evidence):
    for records in evidence[2].values():
        for record in records:
            record['image_sha256'] = 'a' * 64
    assert evaluate(evidence)['success']
    evidence[2]['A'][1]['sim_time'] = evidence[2]['A'][0]['sim_time']
    assert not evaluate(evidence)['success'], 'one acquisition relabelled twice is not independent'


def test_visual_reasoning_can_use_the_full_common_stable_window(evidence):
    record, _, visual, stops = evidence
    # Keep the original images, but continue the same stable window for another
    # four seconds. Do not require observations to land in only the last 3 s.
    for i in range(81, 161):
        sample = copy.deepcopy(record['samples'][-1])
        sample.update(sequence=i+1, sim_time=i*.05, physics_steps=i*25+1)
        for robot in sample['robots'].values():
            robot['sim_time'] = sample['sim_time']
        record['samples'].append(sample)
    record['execution_audit']['navigation_wall_seconds'] = 8
    for stop in stops.values():
        stop['observed_sim_time'] = 8
    assert evaluate(evidence)['success']
    # A later disturbance breaks that common window; earlier views cannot qualify.
    record['samples'][90]['robots']['B']['base_velocity'][0] = 1
    assert not evaluate(evidence)['success']


def test_independent_visual_review_does_not_invent_agent_claims(evidence):
    evidence[2]['A'][0]['agent_claimed_seen_other'] = False
    assert not evaluate(evidence)['success']
    evidence[2]['A'][0]['agent_claimed_seen_other'] = True
    evidence[2]['B'][0]['image_sha256'] = 'not-a-hash'.ljust(64, 'z')
    assert not evaluate(evidence)['success']


def test_conflicting_timestamps_cannot_manufacture_independent_observations(evidence):
    evidence[2]['A'].append({**evidence[2]['A'][0], 'sim_time': 3.8})
    assert not evaluate(evidence)['success']


def test_moving_obstacle_and_missing_geometry_cannot_prove_clear_standing_space(evidence):
    record = evidence[0]
    record['dynamic_geometry_ids'] = [9]
    for sample in record['samples']:
        sample['dynamic_geometry_checked'] = 1
        sample['dynamic_obstacles'] = [dict(geom_id=9, name='chair', bounds=[[4,4,0],[4.5,4.5,1]])]
    assert evaluate(evidence)['success']
    record['samples'][-1]['dynamic_obstacles'][0]['bounds'] = [[1.1,1.1,.1],[1.3,1.3,1]]
    assert not evaluate(evidence)['success']
    record['samples'][-1]['dynamic_obstacles'] = []
    assert not evaluate(evidence)['evidence_valid']


def test_obstacle_filter_must_cover_the_entire_scoring_margin(evidence):
    evidence[0]['dynamic_obstacle_margin_m'] = evidence[1]['body_margin_m']
    assert evaluate(evidence)['success']
    evidence[0]['dynamic_obstacle_margin_m'] /= 2
    assert not evaluate(evidence)['evidence_valid']


def test_repeated_physics_step_cannot_prove_a_stable_time_window(evidence):
    evidence[0]['samples'][-1]['physics_steps'] = evidence[0]['samples'][-2]['physics_steps']
    assert not evaluate(evidence)['evidence_valid']


def test_later_recovery_does_not_erase_terminal_trial_failure(evidence):
    evidence[0]['execution_audit']['terminal_violations'] = [dict(condition='stop_unconfirmed')]
    result = evaluate(evidence)
    assert result['stops_confirmed'] and not result['success'] and not result['terminal_outcomes_valid']


def test_ten_hz_evidence_cannot_pass_the_twenty_hz_contract(evidence):
    record = evidence[0]
    record['samples'] = record['samples'][::2]
    for i, sample in enumerate(record['samples']):
        sample['sequence'] = i + 1
    result = evaluate(evidence)
    assert not result['evidence_valid'] and not result['success']


@pytest.mark.parametrize('field,value', [('world_instance_id','other-world'), ('topology_revision',2)])
def test_visual_review_cannot_cross_world_or_topology(evidence,field,value):
    evidence[2]['A'][0][field] = value
    assert not evaluate(evidence)['success']
