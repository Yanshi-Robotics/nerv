"""Independent scoring must not mistake origin arrival for whole-body arrival."""
import importlib.util
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
    contract={'body_margin_m':.1,'stable_seconds':.2,'joint_velocity_max':.3,
              'regions':{'room':{'interior_xy':[0,0,4,4],'obstacles':[]}}}
    def sample(t):
        return {'robots':{'A':{'bounds':[[1,1,.1],[2,2,1.5]],'status':{'fallen':False},
                              'joint_velocity':[0], 'sim_time':t}}}
    samples=[sample(t) for t in [0,.1,.2,.3]]
    assert module.score(samples,'A','room',contract,stop_confirmed=True,claimed_success=True)['success']
    assert module.score(samples,'A','room',contract,stop_confirmed=False,claimed_success=True)['false_success_claim']
    samples.append(sample(1))
    assert not module.score(samples,'A','room',contract,stop_confirmed=True,claimed_success=False)['success']
