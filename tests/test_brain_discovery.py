"""Real resource services with inert physical allocation; not simulation acceptance."""
import json
import pytest

from test_project_mcp_integration import integrated  # noqa: F401
from nerv.brain_core.core import BrainCore


def test_world_first_allocation_is_scoped_idempotent_and_creates_no_run(integrated, monkeypatch):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        world = f.projects.worlds.create(f.project['world'], f.project['robot'],
                                        topology_policy={'allow_agent_changes':True, 'max_robots':2})
        assert world['roster'] == [] and not f.projects.active_run(f.project['id'])
        grant = dict(world_instance_id=world['world_instance_id'], template_project_id=f.project['id'],
                     max_instances=1, spawn_slots=[dict(x=0.,y=0.,z=1.,yaw=0.)], allow_control=True)
        pair = core.create('Bootstrap', [], ['observe','control'], [grant])
        calls = []
        def add(resource, spawn):
            calls.append((resource, spawn))
            core.resources.save(dict(resource, status='ready'))
        monkeypatch.setattr(f.projects.worlds, 'add_robot', add)
        assert core.transport(pair['token'], 'connect')['brain_instance_id'] is None
        discovered = core.invoke(pair['token'], 'platform_describe', {})
        assert 'spawn' not in json.dumps(discovered) and 'source_revision' not in discovered
        args = dict(world_instance_id=world['world_instance_id'], request_id='allocate-once')
        result = core.invoke(pair['token'], 'robot_allocate', args)
        assert result['status'] == 'complete'
        assert core.invoke(pair['token'], 'robot_allocate', args) == result
        assert len(calls) == 1
        resource = result['resource']
        assert f.projects.active_run(resource['project_id']) is None
        bound = core.invoke(pair['token'], 'brain_attach', {'brain_instance_id':resource['brain_instance_id']})
        assert bound['run_id'] is None and not bound['control']['accepting']
        denied = core.invoke(pair['token'], 'robot_allocate', dict(args, request_id='quota'))
        assert denied['status'] == 'failed' and 'quota' in denied['message']
        with pytest.raises(ValueError, match='different arguments'):
            core.invoke(pair['token'], 'robot_allocate', dict(args, world_instance_id='other'))
        other = core.create('Other', [], ['observe','control'], [grant])
        with pytest.raises(PermissionError):
            core.invoke(other['token'], 'operation_get', {'operation_id':result['operation_id']})
        with pytest.raises(ValueError):
            core.invoke(other['token'], 'brain_get', {}, resource['brain_instance_id'])
    finally:
        core.shutdown()


def test_unapproved_world_cannot_grant_agent_structural_changes(integrated):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        world = f.projects.worlds.create(f.project['world'], f.project['robot'])
        with pytest.raises(ValueError, match='policy'):
            core.create('Denied', [], ['observe'], [dict(world_instance_id=world['world_instance_id'],
                template_project_id=f.project['id'], max_instances=1,
                spawn_slots=[dict(x=0,y=0,z=1,yaw=0)], allow_control=False)])
    finally:
        core.shutdown()
