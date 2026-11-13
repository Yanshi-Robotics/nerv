"""Identity boundaries; inert Runtime fixtures are not navigation evidence."""
import pytest
from test_project_mcp_integration import integrated, ready_binding  # noqa: F401
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


@pytest.mark.parametrize('world_state,robot_state', [('failed','ready'),('stopped','ready'),
                                                   ('ready','failed'),('ready','offline')])
def test_readiness_cannot_outlive_shared_world_or_runtime(integrated,world_state,robot_state):  # noqa: F811 - imported pytest fixture
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        world = f.projects.worlds.create(f.project['world'], f.project['robot'])
        world['status'] = world_state
        f.projects.worlds.save(world)
        resource = core.resources.reserve(f.project, f.pair['connection']['id'])
        resource.update(shared_world=True, world_instance_id=world['world_instance_id'], status=robot_state)
        core.resources.save(resource)
        readiness = f.projects.readiness(f.project['id'])
        assert readiness['ready'] is False
        assert {issue['code'] for issue in readiness['issues']} & {'world_unavailable','runtime_unavailable'}
        assert not f.hub.store.all() and not f.launcher.ensure_body.called
    finally:
        core.shutdown()


def test_unallocated_simulation_run_cannot_create_a_world_or_robot(integrated,monkeypatch):  # noqa: F811
    from unittest.mock import Mock
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    try:
        create = Mock(side_effect=AssertionError('Run must not construct physics'))
        monkeypatch.setattr(f.hub,'new_session',create)
        with pytest.raises(ValueError,match='simulation_resource_required'):
            f.projects.create_run(f.project['id'],operator=True)
        assert not f.projects.store.runs(f.project['id']) and not core.resources.list()
        binding = core.reserve(f.project['id'],f.pair['connection']['id'])
        assert binding['world_instance_id'] is None, 'reservation cannot invent a phantom World'
        with pytest.raises(ValueError,match='simulation_resource_required'):
            core.invoke(f.pair['token'],'run_start',{'project_id':f.project['id'],'request_id':'start'})
        create.assert_not_called()
    finally:
        core.shutdown()


def test_operator_run_only_attaches_existing_resources_and_cannot_rebuild_them(integrated,monkeypatch):  # noqa: F811
    from unittest.mock import Mock
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    try:
        binding = ready_binding(core,f,core.reserve(f.project['id'],f.pair['connection']['id']))
        f.projects.stop_run(f.project['id'],binding['run_id'])  # Cancel a metadata-only reservation.
        prepare = Mock(side_effect=AssertionError('Run must not start or recover a World'))
        monkeypatch.setattr(f.projects.worlds,'prepare',prepare)
        monkeypatch.setattr(f.hub,'new_session',Mock(side_effect=AssertionError('Run must not spawn')))
        result = f.projects.create_run(f.project['id'],operator=True)
        for field in ('brain_instance_id','robot_instance_id','world_instance_id'):
            assert result['run'][field] == binding[field]
        assert result['run']['id'] != binding['run_id']
        assert not f.launcher.ensure_body.called and not f.launcher.ensure_shared_world.called
        before = f.projects.active_run(f.project['id'])
        with pytest.raises(ValueError,match='resource lifecycle'):
            f.projects.rebuild(f.project['id'],{})
        assert f.projects.active_run(f.project['id']) == before
        prepare.assert_not_called()
    finally:
        core.shutdown()


def test_run_start_waits_for_world_transaction_and_rechecks_its_result(integrated):  # noqa: F811
    import threading
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    errors, entered = [], threading.Event()
    try:
        binding = ready_binding(core,f,core.reserve(f.project['id'],f.pair['connection']['id']))
        def start():
            entered.set()
            try:
                f.projects.create_run(f.project['id'])
            except ValueError as error:
                errors.append(str(error))
        with f.projects.worlds.lock:
            thread = threading.Thread(target=start)
            thread.start()
            assert entered.wait(1)
            assert not f.hub.store.all()
            world = f.projects.worlds.get(binding['world_instance_id'])
            world['status'] = 'failed'
            f.projects.worlds.save(world)
        thread.join(2)
        assert not thread.is_alive() and errors and 'world_unavailable' in errors[0]
        assert not f.hub.store.all() and not f.launcher.ensure_shared_world.called
    finally:
        core.shutdown()


def test_queued_run_start_cannot_replace_an_ended_allocation(integrated):  # noqa: F811
    import threading
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    errors, entered = [], threading.Event()
    try:
        ready_binding(core,f,core.reserve(f.project['id'],f.pair['connection']['id']))
        original = f.projects.active_run(f.project['id'])
        def start():
            entered.set()
            try:
                f.projects.create_run(f.project['id'],expected_run={'id':original['id']})
            except ValueError as error:
                errors.append(str(error))
        with f.projects.worlds.lock:
            thread = threading.Thread(target=start)
            thread.start()
            assert entered.wait(1)
            f.projects.store.save_run(f.project['id'],dict(original,status='ended'))
        thread.join(2)
        assert not thread.is_alive() and errors and 'allocation changed' in errors[0]
        assert len(f.projects.store.runs(f.project['id'])) == 1 and not f.hub.store.all()
    finally:
        core.shutdown()


@pytest.mark.parametrize('fault',['runtime','world','configuration'])
def test_live_resource_failure_cannot_be_hidden_by_persisted_ready_state(integrated,fault):  # noqa: F811
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    try:
        binding = ready_binding(core,f,core.reserve(f.project['id'],f.pair['connection']['id']))
        if fault == 'configuration':
            world = f.projects.worlds.get(binding['world_instance_id'])
            world['robot_profile'] = 'different-profile'
            f.projects.worlds.save(world)
        else:
            key = 'body:'+binding['robot_instance_id'] if fault == 'runtime' else 'shared_world:'+binding['world_instance_id']
            f.launcher.nodes[key].alive = lambda:False
        assert not f.projects.readiness(f.project['id'])['ready']
        with pytest.raises(ValueError,match='configuration'):
            f.projects.create_run(f.project['id'])
        assert not f.hub.store.all() and not f.launcher.ensure_shared_world.called
    finally:
        core.shutdown()


def test_ending_and_restarting_a_run_preserves_the_existing_entities(integrated,monkeypatch):  # noqa: F811
    from unittest.mock import Mock
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    try:
        binding = ready_binding(core,f,core.reserve(f.project['id'],f.pair['connection']['id']))
        forbidden = Mock(side_effect=AssertionError('Run must not destroy physics'))
        monkeypatch.setattr(f.hub,'stop_simulation',forbidden)
        first = f.projects.create_run(f.project['id'])
        assert f.projects.stop_run(f.project['id'],first['run']['id'])['ok']
        assert f.projects.worlds.get(binding['world_instance_id'])['status'] == 'ready'
        assert core.resources.for_project(f.project['id'])['status'] == 'ready'
        second = f.projects.create_run(f.project['id'])
        for field in ('brain_instance_id','robot_instance_id','world_instance_id'):
            assert second['run'][field] == first['run'][field] == binding[field]
        assert second['run']['id'] != first['run']['id']
        assert not core.control(f.project['id'])['accepting']
        forbidden.assert_not_called()
        f.launcher.stop.assert_not_called()
    finally:
        core.shutdown()


def test_owned_standby_run_can_end_without_motion_but_peers_cannot(integrated, monkeypatch):  # noqa: F811 - imported pytest fixture
    from unittest.mock import Mock
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    pid = f.project['id']
    try:
        owner = core.create('Owner', [pid], ['observe', 'control'])
        peer = core.create('Peer', [pid], ['observe', 'control'])
        binding = core.reserve(pid, owner['connection']['id'])
        binding = ready_binding(core,f,binding)
        core.invoke(owner['token'], 'run_start', {'project_id':pid, 'request_id':'start'})
        assert not core.control(pid)['accepting']
        stop = Mock(return_value={'ok':False, 'message':'Stop is unconfirmed'})
        monkeypatch.setattr(f.projects, 'stop_run', stop)
        with pytest.raises(PermissionError, match='own the Run'):
            core.invoke(peer['token'], 'run_end', {'project_id':pid, 'request_id':'peer-end'})
        stop.assert_not_called()
        result = core.invoke(owner['token'], 'run_end', {'project_id':pid, 'request_id':'owner-end'})
        assert not result['ok']  # Authorization never turns an unconfirmed stop into success.
        stop.assert_called_once_with(pid, binding['run_id'])
        assert core.resources.for_project(pid)['brain_instance_id'] == binding['brain_instance_id']
        core.set_control(pid, accepting=True, owner_id=peer['connection']['id'])
        with pytest.raises(PermissionError, match='own the Run'):
            core.invoke(owner['token'], 'run_end', {'project_id':pid, 'request_id':'after-transfer'})
        assert stop.call_count == 1
    finally:
        core.shutdown()


def test_shared_run_rejects_unknown_sensor_before_creating_session(integrated, monkeypatch):  # noqa: F811 - imported pytest fixture
    f = integrated
    core = BrainCore(f.hub,f.projects,f.gw.root)
    try:
        binding = ready_binding(core,f,core.reserve(f.project['id'],f.pair['connection']['id']))
        run = f.projects.active_run(f.project['id'])
        run['snapshot'] = dict(f.project,sensors=['camera:head'])
        with pytest.raises(ValueError, match='does not publish sensor'):
            f.projects.worlds.attach(run)
        assert not f.hub.store.all() and binding['robot_instance_id']
    finally:
        core.shutdown()


def test_world_restart_refreshes_persistent_runtime_attachment_without_a_run(integrated, monkeypatch):  # noqa: F811 - imported pytest fixture
    from types import SimpleNamespace
    from unittest.mock import Mock
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        pair = core.create('Observer', [f.project['id']], ['observe'])
        worlds = f.projects.worlds
        world = worlds.create(f.project['world'], f.project['robot'])
        resource = core.resources.reserve(f.project, pair['connection']['id'])
        resource.update(world_instance_id=world['world_instance_id'], shared_world=True,
                        status='offline', runtime_epoch='old-runtime')
        core.resources.save(resource)
        world.update(status='stopped', roster=[dict(robot_instance_id=resource['robot_instance_id'],
                     project_id=f.project['id'], run_id=None, spawn=dict(x=0,y=0,z=1,yaw=0))])
        worlds.save(world)
        spec = f.hub.registry.world(f.project['world'])
        spec.physics = {}
        spec.model_copy = lambda **kwargs: spec
        body = f.hub.registry.body(f.project['robot'])
        body.actuators, body.dir = {}, 'fixture'
        body.model_copy = lambda **kwargs: body
        handle = SimpleNamespace(meta={'epoch':'new-world', 'configuration':{}})
        runtime = SimpleNamespace(meta={'epoch':'new-runtime'})
        f.launcher.nodes['world:' + world['world_instance_id'] + '/' + resource['robot_instance_id']] = handle
        f.launcher.ensure_shared_world = Mock(return_value=handle)
        f.launcher.ensure_runtime = Mock(return_value=runtime)
        monkeypatch.setattr(f.robot, 'health', lambda: {'epoch':'new-runtime'})
        restarted = worlds.prepare(world, restart=True)
        assert restarted['generation'] == world['generation'] + 1
        current = core.resources.get(resource['robot_instance_id'])
        assert current['runtime_epoch'] == 'new-runtime' and current['status'] == 'ready'
        observation = core.invoke(pair['token'], 'observe', {'project_id':f.project['id']})
        assert observation['robot_epoch'] == 'new-runtime'
        assert observation['run_id'] is None and not observation['motion_authorized']
        assert not core._leases and not f.projects.active_run(f.project['id'])
    finally:
        core.shutdown()


def test_reserved_run_starts_once_and_keeps_identity(integrated):  # noqa: F811
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        pair = core.create('Bound Agent', [f.project['id']], ['observe', 'control'])
        binding = core.reserve(f.project['id'], pair['connection']['id'])
        binding = ready_binding(core,f,binding)
        args = {'project_id': f.project['id'], 'request_id': 'x' * 128}
        result = core.invoke(pair['token'], 'run_start', args)
        assert result['run_id'] == binding['run_id']
        assert core.invoke(pair['token'], 'run_start', args)['run_id'] == binding['run_id']
        session = f.projects.require_session(f.project['id'])
        assert session.brain_instance_id == binding['brain_instance_id']
        assert session.robot_instance_id == binding['robot_instance_id']
        assert session.armed is False
        f.launcher.ensure_body.assert_not_called()
        f.launcher.ensure_shared_world.assert_not_called()
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
        bid = ready_binding(core,f,core.reserve(f.project['id'], pair['connection']['id']))['brain_instance_id']
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
        bid=ready_binding(core,f,core.reserve(f.project['id'],pair['connection']['id']))['brain_instance_id']
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


def test_robot_and_brain_identity_survive_run_end_and_new_run(integrated):  # noqa: F811 - imported pytest fixture
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


def test_observer_transport_does_not_revoke_the_controller(integrated):  # noqa: F811 - imported pytest fixture
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        owner = core.create('Owner', [f.project['id']], ['observe','control'])
        observer = core.create('Observer', [f.project['id']], ['observe'])
        bid = ready_binding(core,f,core.reserve(f.project['id'],owner['connection']['id']))['brain_instance_id']
        core.invoke(owner['token'],'run_start',{'project_id':f.project['id'],'request_id':'start'},bid)
        core.set_control(f.project['id'],accepting=True,owner_id=owner['connection']['id'])
        before = core.control(f.project['id'])
        connection = core.transport(observer['token'],'connect',bid)
        core.transport(observer['token'],'disconnect',bid,connection['transport_id'])
        assert core.control(f.project['id']) == before
    finally:
        core.shutdown()


def test_resource_without_run_becomes_failed_when_world_disappears(integrated):  # noqa: F811 - imported pytest fixture
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


def test_delayed_world_health_probe_cannot_overwrite_new_topology(integrated, monkeypatch):  # noqa: F811 - imported pytest fixture
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


def test_no_run_observation_rejects_runtime_change_and_never_grants_control(integrated, monkeypatch):  # noqa: F811 - imported pytest fixture
    f=integrated
    core=BrainCore(f.hub,f.projects,f.gw.root)
    try:
        pair=core.create('Observer',[f.project['id']],['observe'])
        resource=core.resources.reserve(f.project,pair['connection']['id'])
        resource.update(status='ready',runtime_epoch='fixture-epoch')
        core.resources.save(resource)
        first=core.invoke(pair['token'],'observe',{'project_id':f.project['id']},resource['brain_instance_id'])
        assert first['run_id'] is None and not first['motion_authorized']
        assert first['robot_epoch']=='fixture-epoch' and first['observation_id']
        assert 'receipt_id' not in first and not core._receipts
        monkeypatch.setattr(f.robot, 'health', lambda:{'epoch':'replacement-runtime'})
        with pytest.raises(ValueError, match='attachment changed'):
            core.invoke(pair['token'],'observe',{'project_id':f.project['id']},resource['brain_instance_id'])
        monkeypatch.setattr(f.robot, 'health', lambda:{'epoch':'fixture-epoch'})
        original=f.robot.perceive
        def changed():
            result=original()
            core.resources.save(dict(resource,status='offline'))
            return result
        monkeypatch.setattr(f.robot,'perceive',changed)
        with pytest.raises(ValueError,match='changed while reading'):
            core.invoke(pair['token'],'observe',{'project_id':f.project['id']},resource['brain_instance_id'])
    finally:
        core.shutdown()


@pytest.mark.parametrize('fault', ['stop', 'world_loss', 'runtime_loss'])
def test_new_operator_run_remains_in_world_failure_scope(integrated, fault):  # noqa: F811
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        binding = ready_binding(core, f, core.reserve(f.project['id'], f.pair['connection']['id']))
        f.projects.stop_run(f.project['id'], binding['run_id'])
        created = f.projects.create_run(f.project['id'], operator=True)
        sid, run_id = created['session']['id'], created['run']['id']
        f.hub.store.set_armed(sid, True)
        if fault == 'stop':
            f.projects.worlds.stop(binding['world_instance_id'])
        else:
            key = ('shared_world:' + binding['world_instance_id'] if fault == 'world_loss'
                   else 'body:' + binding['robot_instance_id'])
            f.launcher.nodes[key].alive = lambda: False
            f.projects.worlds.check_health()
        assert f.projects.active_run(f.project['id']) is None
        assert not f.hub.store.get(sid).armed
        assert next(r for r in f.projects.store.runs(f.project['id']) if r['id'] == run_id)['status'] in {'failed', 'interrupted'}
    finally:
        core.shutdown()


def test_reservation_waits_for_world_stop_without_restoring_stale_metadata(integrated):  # noqa: F811
    import threading
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    entered, errors = threading.Event(), []
    try:
        binding = ready_binding(core, f, core.reserve(f.project['id'], f.pair['connection']['id']))
        f.projects.stop_run(f.project['id'], binding['run_id'])
        underlying = f.projects.worlds.lock
        class PendingLock:
            def __enter__(self):
                entered.set()
                return underlying.__enter__()
            def __exit__(self, *args):
                return underlying.__exit__(*args)
        f.projects.worlds.lock = PendingLock()
        def reserve():
            try:
                core.reserve(f.project['id'], f.pair['connection']['id'])
            except Exception as error:
                errors.append(error)
        with underlying:
            thread = threading.Thread(target=reserve)
            thread.start()
            assert entered.wait(1)
            stopped = f.projects.worlds.stop(binding['world_instance_id'])['world']
        thread.join(2)
        world = f.projects.worlds.get(binding['world_instance_id'])
        assert not thread.is_alive() and not errors
        assert world['status'] == 'stopped' and world['generation'] == stopped['generation']
        assert world['roster'][0]['run_id'] == f.projects.active_run(f.project['id'])['id']
    finally:
        core.shutdown()


def test_waiting_run_start_cannot_block_another_project_stop(integrated):  # noqa: F811
    import threading
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    started, stopped, errors = threading.Event(), threading.Event(), []
    try:
        ready_binding(core, f, core.reserve(f.project['id'], f.pair['connection']['id']))
        peer = f.projects.create(dict(name='Independent project', mode='sim',
            robot=f.project['robot'], world=f.project['world']))
        pair = core.create('Peer', [peer['id']], ['observe', 'control'])
        reserved = core.reserve(peer['id'], pair['connection']['id'])
        # Instrument the actual wait, rather than assuming thread scheduling.
        underlying = f.projects.worlds.lock
        class PendingLock:
            def __enter__(self):
                started.set()
                return underlying.__enter__()
            def __exit__(self, *args):
                return underlying.__exit__(*args)
        f.projects.worlds.lock = PendingLock()
        def start():
            try:
                f.projects.create_run(f.project['id'])
            except Exception as error:
                errors.append(error)
        def stop():
            try:
                f.projects.stop_run(peer['id'], reserved['run_id'])
                stopped.set()
            except Exception as error:
                errors.append(error)
        with underlying:
            starter = threading.Thread(target=start)
            starter.start()
            assert started.wait(1)
            stopper = threading.Thread(target=stop)
            stopper.start()
            was_independent = stopped.wait(1)
        starter.join(2)
        stopper.join(2)
        assert was_independent, 'Stop waited behind an unrelated World transaction'
        assert not errors and not starter.is_alive() and not stopper.is_alive()
    finally:
        core.shutdown()
