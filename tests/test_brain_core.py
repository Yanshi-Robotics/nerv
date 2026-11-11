"""Identity boundaries; inert Runtime fixtures are not navigation evidence."""
import pytest
from test_project_mcp_integration import integrated  # noqa: F401
from nerv.brain_core.core import BrainCore


def test_binding_is_operator_owned_and_cannot_cross_projects(integrated):  # noqa: F811
    hub, projects, gateway, pid = integrated.hub, integrated.projects, integrated.gw, integrated.project["id"]
    core = BrainCore(hub, projects, gateway.root)
    try:
        connection = core.create('A', [pid], ['observe', 'control'])
        token = connection['token']
        with pytest.raises(ValueError, match='operator-bound'):
            core.invoke(token, 'brain_get', {})
        bound = core.reserve(pid, connection['connection']['id'])
        assert core.invoke(token, 'brain_get', {}, bound['brain_instance_id'])['run_id'] == bound['run_id']
        with pytest.raises(PermissionError):
            core.invoke(token, 'project_get', {'project_id': 'another'}, bound['brain_instance_id'])
        other = core.create('B', [pid], ['observe', 'control'])
        with pytest.raises(ValueError, match='replacing'):
            core.reserve(pid, other['connection']['id'])
        assert core.invoke(other['token'], 'brain_get', {}, bound['brain_instance_id'])['brain_instance_id'] == bound['brain_instance_id']
    finally:
        core.shutdown()


def test_internal_provider_endpoints_are_tombstones(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    client = TestClient(server.app)
    for url, payload in [('/api/vlm/model', {}), ('/api/credentials/key', {'value': 'private-test'})]:
        response = client.put(url, json=payload)
        assert response.status_code == 410
        assert 'private-test' not in response.text
    assert client.get('/api/brains').json() == []


def test_reserved_run_starts_once_and_keeps_identity(integrated):  # noqa: F811
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        pair = core.create('Bound Agent', [f.project['id']], ['observe', 'control'])
        binding = core.reserve(f.project['id'], pair['connection']['id'])
        args = {'project_id': f.project['id'], 'request_id': 'x' * 128}
        result = core.invoke(pair['token'], 'run_start', args)
        assert result['run_id'] == binding['run_id']
        assert core.invoke(pair['token'], 'run_start', args)['run_id'] == binding['run_id']
        session = f.projects.require_session(f.project['id'])
        assert session.brain_instance_id == binding['brain_instance_id']
        assert session.robot_instance_id == binding['robot_instance_id']
        assert session.armed is False
        f.launcher.ensure_body.assert_called_once()
    finally:
        core.shutdown()


def test_shared_composition_requires_reserved_revisions(integrated):  # noqa: F811
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        second = f.projects.store.create({'name':'Second','mode':'sim','robot':f.project['robot'],'world':f.project['world']})
        slots = []
        bindings = []
        for i, project in enumerate([f.project, second]):
            pair = core.create(project['name'], [project['id']], ['observe','control'])
            bindings.append(core.reserve(project['id'],pair['connection']['id']))
            slots.append({'project_id':project['id'],'spawn':{'x':i*2.,'y':0.,'z':1.,'yaw':0.}})
        frozen = f.projects.worlds.configure(slots)
        assert len({r['robot_instance_id'] for r in frozen['roster']}) == 2
        assert all(f.projects.active_run(p['project_id'])['world_instance_id'] == frozen['world_instance_id'] for p in slots)
        assert not f.launcher.ensure_body.called
        with pytest.raises(ValueError, match='unstarted'):
            f.projects.worlds.configure(slots)
    finally:
        core.shutdown()


def test_transport_expiry_and_reconnect_never_restore_motion(integrated):  # noqa: F811
    import time
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        pair = core.create('Bound', [f.project['id']], ['observe', 'control'])
        bid = core.reserve(f.project['id'], pair['connection']['id'])['brain_instance_id']
        core.invoke(pair['token'], 'run_start', {'project_id':f.project['id'], 'request_id':'run'}, bid)
        first = core.transport(pair['token'], 'connect', bid)
        with pytest.raises(ValueError, match='live MCP'):
            core.transport(pair['token'], 'connect', bid)
        core.set_control(f.project['id'], accepting=True, owner_id=pair['connection']['id'])
        core._transports[bid + ':' + pair['connection']['id']]['deadline'] = time.monotonic() - 1
        core.check_expired()
        assert not core.control(f.project['id'])['accepting']
        f.hub.stop_robot.assert_called()
        with pytest.raises(PermissionError, match='generation'):
            core.invoke(pair['token'], 'brain_get', {}, bid, first['transport_id'])
        replacement = core.transport(pair['token'], 'connect', bid)
        assert replacement['transport_id'] != first['transport_id']
        assert not core.control(f.project['id'])['accepting']
        with pytest.raises(ValueError, match='generation'):
            core.transport(pair['token'], 'heartbeat', bid, first['transport_id'])
        assert core.invoke(pair['token'], 'brain_get', {}, bid, replacement['transport_id'])['brain_instance_id'] == bid
        f.robot.invoke.assert_not_called()
    finally:
        core.shutdown()


def test_retry_keeps_original_control_generation(integrated):  # noqa: F811
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        pair=core.create('Bound', [f.project['id']], ['observe','control'])
        bid=core.reserve(f.project['id'],pair['connection']['id'])['brain_instance_id']
        args={'project_id':f.project['id'],'request_id':'once'}
        first=core.invoke(pair['token'],'run_start',args,bid)
        core.set_control(f.project['id'],accepting=True,owner_id=pair['connection']['id'])
        assert core.control(f.project['id'])['control_generation'] > first['control_generation']
        assert core.invoke(pair['token'],'run_start',args,bid) == first
    finally:
        core.shutdown()


def test_scene_revision_covers_geometry_and_ignores_private_rules(tmp_path):
    from types import SimpleNamespace
    from nerv.brain_core.worlds import source_revision
    (tmp_path/'scene.xml').write_text('<mujoco/>')
    (tmp_path/'mesh.obj').write_text('v 0 0 0')
    spec=SimpleNamespace(assets_root=str(tmp_path))
    first=source_revision(spec)
    (tmp_path/'AGENTS.md').write_text('Private rule')
    assert source_revision(spec)==first
    (tmp_path/'mesh.obj').write_text('v 1 0 0')
    assert source_revision(spec)!=first


def test_shared_runtime_failure_is_local_and_world_failure_invalidates_peers(integrated, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        second=f.projects.store.create({'name':'Peer','mode':'sim','robot':f.project['robot'],'world':f.project['world']})
        slots=[]
        for i,p in enumerate([f.project,second]):
            pair=core.create(p['name'],[p['id']],['observe','control'])
            core.reserve(p['id'],pair['connection']['id'])
            slots.append({'project_id':p['id'],'spawn':{'x':float(i),'y':0.,'z':1.,'yaw':0.}})
        world=f.projects.worlds.configure(slots)
        world.update(status='ready',epoch='world_epoch')
        f.projects.worlds.save(world)
        owner=SimpleNamespace(url='http://fixture.invalid',alive=lambda:True)
        f.launcher.nodes['shared_world:'+world['world_instance_id']]=owner
        for slot in world['roster']:
            run=f.projects.active_run(slot['project_id'])
            session,_=f.hub.store.new('operator',slot['robot_instance_id'],world['world_instance_id'],epoch='world_epoch')
            run.update(status='running',session_id=session.id)
            f.projects.store.save_run(run['project_id'],run)
            f.launcher.nodes['body:'+slot['robot_instance_id']]=SimpleNamespace(alive=lambda:True)
        monkeypatch.setattr('httpx.get',lambda *a,**k:SimpleNamespace(json=lambda:{'ok':True,'epoch':'world_epoch'}))
        first,peer=world['roster']
        f.launcher.nodes.pop('body:'+first['robot_instance_id'])
        assert f.projects.worlds.check_health()==[first['project_id']]
        assert f.projects.active_run(first['project_id']) is None
        assert f.projects.active_run(peer['project_id'])['status']=='running'
        owner.alive=lambda:False
        f.projects.worlds._last_health=0
        assert f.projects.worlds.check_health()==[peer['project_id']]
        assert f.projects.active_run(peer['project_id']) is None
        assert f.projects.worlds.get(world['world_instance_id'])['status']=='failed'
    finally:
        core.shutdown()


def test_robot_and_brain_identity_survive_run_end_and_new_run(integrated):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        pair = core.create('Persistent', [f.project['id']], ['observe', 'control'])
        cid = pair['connection']['id']
        first = core.reserve(f.project['id'], cid)
        f.projects.stop_run(f.project['id'], first['run_id'])
        history = core.invoke(pair['token'], 'brain_get', {}, first['brain_instance_id'])
        assert history['status'] == 'ended'
        second = core.reserve(f.project['id'], cid)
        assert second['run_id'] != first['run_id']
        assert second['brain_instance_id'] == first['brain_instance_id']
        assert second['robot_instance_id'] == first['robot_instance_id']
        assert not second['control']['accepting']
        assert len(core.resources.list()) == 1
    finally:
        core.shutdown()


def test_observer_transport_does_not_revoke_the_controller(integrated):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        owner = core.create('Owner', [f.project['id']], ['observe','control'])
        observer = core.create('Observer', [f.project['id']], ['observe'])
        bid = core.reserve(f.project['id'],owner['connection']['id'])['brain_instance_id']
        core.invoke(owner['token'],'run_start',{'project_id':f.project['id'],'request_id':'start'},bid)
        core.set_control(f.project['id'],accepting=True,owner_id=owner['connection']['id'])
        before = core.control(f.project['id'])
        connection = core.transport(observer['token'],'connect',bid)
        core.transport(observer['token'],'disconnect',bid,connection['transport_id'])
        assert core.control(f.project['id']) == before
    finally:
        core.shutdown()


def test_resource_without_run_becomes_failed_when_world_disappears(integrated):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        resource = core.resources.reserve(f.project, 'agent_fixture')
        world = f.projects.worlds.create(f.project['world'], f.project['robot'])
        resource.update(world_instance_id=world['world_instance_id'], shared_world=True, status='ready')
        core.resources.save(resource)
        world.update(status='ready', epoch='lost', roster=[dict(robot_instance_id=resource['robot_instance_id'],
            project_id=f.project['id'], run_id=None)])
        f.projects.worlds.save(world)
        assert f.projects.active_run(f.project['id']) is None
        assert f.projects.worlds.check_health() == [f.project['id']]
        assert core.resources.get(resource['robot_instance_id'])['status'] == 'failed'
        assert f.projects.worlds.get(world['world_instance_id'])['status'] == 'failed'
    finally:
        core.shutdown()


def test_delayed_world_health_probe_cannot_overwrite_new_topology(integrated, monkeypatch):
    from types import SimpleNamespace
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        world = f.projects.worlds.create(f.project['world'], f.project['robot'])
        world.update(status='ready', epoch='same-world')
        f.projects.worlds.save(world)
        f.launcher.nodes['shared_world:' + world['world_instance_id']] = SimpleNamespace(
            url='http://fixture.invalid', alive=lambda:True)
        def delayed(*args, **kwargs):
            changed = dict(world, generation=2, topology_revision=2)
            f.projects.worlds.save(changed)
            return SimpleNamespace(json=lambda:{'ok':False})
        monkeypatch.setattr('httpx.get', delayed)
        assert f.projects.worlds.check_health() == []
        current = f.projects.worlds.get(world['world_instance_id'])
        assert current['generation'] == 2 and current['status'] == 'ready'
    finally:
        core.shutdown()
