"""A previous process's successful health probe is history, not recovered liveness."""
import json
from types import SimpleNamespace

import pytest

from nerv.brain_core.worlds import WorldInstances


@pytest.mark.parametrize('status', ['ready', 'changing', 'preparing', 'interrupted', 'stopped'])
def test_recovered_world_does_not_report_stale_healthy_probe(tmp_path, status):
    root = tmp_path/'world-instances'
    root.mkdir()
    old = {'state':'healthy','observed_at':1,'process_alive':True,'observed_epoch':'old'}
    value = dict(world_instance_id='world_fixture', status=status, epoch='old', health=old)
    (root/'world_fixture.json').write_text(json.dumps(value))
    worlds = WorldInstances(SimpleNamespace(store=SimpleNamespace(root=tmp_path/'projects'), hub=object()))
    recovered = worlds.get('world_fixture')
    assert recovered['status'] == ('interrupted' if status in {'ready','changing','preparing'} else status)
    assert recovered['health']['state']=='unknown' and recovered['health']['reason']=='host_restarted'
    assert recovered['health']['observed_at'] is None and recovered['health']['process_alive'] is None
    assert recovered['last_known_health']==old
