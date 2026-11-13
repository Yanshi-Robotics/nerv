"""File tampering and identity mixups must fail before human review is scored."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from tools.acceptance.visual_evidence import bind_review


@pytest.fixture
def retained(tmp_path):
    image = tmp_path/'0001-2.png'
    image.write_bytes(b'original-camera-bytes')
    identity = dict(brain_instance_id='brain-A',run_id='run-A',robot_instance_id='robot-A',
                    world_instance_id='world',world_epoch='epoch',topology_revision=3)
    frame = {k:identity[k] for k in ('robot_instance_id','world_instance_id','world_epoch','topology_revision')}
    frame.update(source_metadata_available=True, source_sequence=19,captured_sim_time=4.25,
                 image_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
    observation = {**identity,'receipt_id':'receipt','state':{'image_frames':{'camera:head':frame}}}
    event = dict(sequence=1,request={'operation':'observe'},result={'isError':False,'content':[
        {'type':'text','text':json.dumps(observation)}, {'type':'text','text':'Sensor: camera:head'},
        {'type':'image','mimeType':'image/png','path':str(image)}]})
    protocol=tmp_path/'protocol.jsonl'
    protocol.write_text(json.dumps(event)+'\n')
    return protocol,identity,image,event,observation


def review(retained):
    protocol,identity,*_ = retained
    return bind_review(protocol,identity,relay_sequence=1,sensor='camera:head',
                       agent_claimed_seen_other=True,seen_other_reviewed=True)


def test_original_acquisition_is_bound_without_inventing_a_view(retained):
    result=review(retained)
    assert result['observation_sequence']==19 and result['sim_time']==4.25
    assert result['image_sha256']==hashlib.sha256(retained[2].read_bytes()).hexdigest()
    result=bind_review(retained[0],retained[1],relay_sequence=1,sensor='camera:head',
                       agent_claimed_seen_other=False,seen_other_reviewed=False)
    assert result['agent_claimed_seen_other'] is False and result['seen_other_reviewed'] is False


@pytest.mark.parametrize('fault',['bytes','world','topology','run','missing_metadata','duplicate','outside'])
def test_changed_bytes_or_mixed_originals_are_rejected(retained,fault,tmp_path):
    protocol,identity,image,event,observation=retained
    frame=observation['state']['image_frames']['camera:head']
    if fault=='bytes':image.write_bytes(b'changed')
    if fault=='world':frame['world_instance_id']='other'
    if fault=='topology':frame['topology_revision']=4
    if fault=='run':observation['run_id']='other'
    if fault=='missing_metadata':frame['source_metadata_available']=False
    if fault=='outside':
        other=tmp_path/'other';other.mkdir();outside=other/'image.png';outside.write_bytes(image.read_bytes())
        event['result']['content'][-1]['path']=str(outside)
    event['result']['content'][0]['text']=json.dumps(observation)
    protocol.write_text(json.dumps(event)+'\n'+(json.dumps(event)+'\n' if fault=='duplicate' else ''))
    with pytest.raises(ValueError):review(retained)
