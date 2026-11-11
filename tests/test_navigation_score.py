"""Independent scoring must not mistake origin arrival for whole-body arrival."""
import importlib.util
import copy

import pytest
from pathlib import Path

spec = importlib.util.spec_from_file_location('navigation_score', Path(__file__).parents[1] / 'tools/acceptance/score_navigation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_body_margin_obstacle_and_fall_reject_false_arrival():
    region = {'interior_xy':[0,0,4,4], 'obstacles':[]}
    contract = {'body_margin_m':.1}
    robot = {'bounds':[[1,1,.1],[2,2,1.5]],'status':{'fallen':False}}
    assert module.geometry_verdict(robot,region,contract)['inside']
    region['obstacles']=[{'name':'table','bounds':[[1.5,1.5,.5],[2.5,2.5,1]]}]
    assert not module.geometry_verdict(robot,region,contract)['inside']
    region['obstacles']=[]
    robot['bounds'][0][0]=.05
    assert not module.geometry_verdict(robot,region,contract)['inside']
    robot['bounds'][0][0]=1
    robot['status']['fallen']=True
    assert not module.geometry_verdict(robot,region,contract)['inside']


def test_stop_confirmation_and_continuous_stability_are_required():
    contract={'body_margin_m':.1,'stable_seconds':.2,'joint_velocity_max':.3, 'max_sample_gap_s':.15,
              'base_linear_velocity_max':.05, 'base_angular_velocity_max':.1,
              'regions':{'room':{'interior_xy':[0,0,4,4],'obstacles':[]}}}
    def sample(t):
        return {'robots':{'A':{'bounds':[[1,1,.1],[2,2,1.5]],'status':{'fallen':False},
                              'joint_velocity':[0], 'base_velocity':[0]*6, 'epoch':'world-1', 'sim_time':t}}}
    samples=[sample(t) for t in [0,.1,.2,.3]]
    assert module.score(samples,'A','room',contract,stop_confirmed=True,claimed_success=True)['success']
    assert module.score(samples,'A','room',contract,stop_confirmed=False,claimed_success=True)['false_success_claim']
    samples.append(sample(1))
    assert not module.score(samples,'A','room',contract,stop_confirmed=True,claimed_success=False)['success']


@pytest.mark.parametrize("change", [
    {"joint_velocity": []}, {"joint_velocity": [float("nan")]},
    {"base_velocity": [0, 0, 0]}, {"base_velocity": [1, 0, 0, 0, 0, 0]},
    {"sim_time": float("nan")}, {"epoch": "world-2"}, {"bounds": [[0], [1]]},
])
def test_incomplete_or_moving_evidence_cannot_pass(change):
    contract = dict(body_margin_m=.1, stable_seconds=.2, max_sample_gap_s=.15,
                    joint_velocity_max=.3, base_linear_velocity_max=.05,
                    base_angular_velocity_max=.1, floor_tolerance_m=.05,
                    regions={"room": dict(interior_xy=[0,0,4,4], floor_z=0, obstacles=[])})
    robot = dict(bounds=[[1,1,0],[2,2,1.5]], status={"fallen":False},
                 joint_velocity=[0], base_velocity=[0]*6, epoch="world-1")
    samples = [{"robots":{"A":dict(copy.deepcopy(robot), sim_time=t)}} for t in [0,.1,.2,.3]]
    samples[-1]["robots"]["A"].update(change)
    result = module.score(samples, "A", "room", contract, stop_confirmed=True, claimed_success=True)
    assert not result["success"] and result["false_success_claim"]


def test_wrong_floor_rejected_even_when_xy_and_joints_are_correct():
    robot = dict(bounds=[[1,1,3],[2,2,4.5]], status={"fallen":False})
    result = module.geometry_verdict(robot, dict(interior_xy=[0,0,4,4], floor_z=0, obstacles=[]),
                                     dict(body_margin_m=.1, floor_tolerance_m=.05))
    assert result["reason"] == "wrong_floor_or_unsupported"
