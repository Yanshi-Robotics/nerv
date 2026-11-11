"""Load both retained contract layouts without calling ONNX or changing artifacts."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from nerv.body.families.humanoid import PolicyRelease

@pytest.mark.parametrize('version',[1,2])
def test_observation_scale_and_order_are_read_from_each_contract(tmp_path,monkeypatch,version):
    terms=['base_ang_vel','projected_gravity','velocity_commands','joint_pos_rel','joint_vel_rel','last_action']
    scales={name:1. for name in terms};scales['base_ang_vel']=.2
    contract=dict(joint_names=['joint'],default_joint_pos=[0],history_length=5,
        action={'scale':.25},timing={'policy_dt_s':.02},gains={'joint':{'kp':10,'kd':1,'effort_limit':2}})
    if version==1:contract.update(term_scales=scales,obs_terms=[{'term':name} for name in terms])
    else:contract.update(schema_version=2,obs_terms=[dict(name=name,scale=None if scales[name]==1 else scales[name],history_length=5) for name in terms])
    (tmp_path/'contract.json').write_text(json.dumps(contract))
    (tmp_path/'release.yaml').write_text('pd_mode: implicit\n')
    (tmp_path/'policy.onnx').write_bytes(b'explicit inert fixture')
    session=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name='observation')])
    monkeypatch.setitem(sys.modules,'onnxruntime',SimpleNamespace(disable_telemetry_events=lambda:None,InferenceSession=lambda *a,**k:session))
    policy=PolicyRelease(str(tmp_path))
    assert policy.term_order==tuple(terms) and policy.scales==scales and policy.history==5
