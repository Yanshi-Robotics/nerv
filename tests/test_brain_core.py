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
        with pytest.raises(ValueError, match='operator-bound'):
            core.invoke(other['token'], 'brain_get', {}, bound['brain_instance_id'])
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
        core._transports[bid]['deadline'] = time.monotonic() - 1
        core.check_expired()
        assert not core.control(f.project['id'])['accepting']
        f.hub.stop_robot.assert_called()
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
