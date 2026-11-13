"""Registered-device lifecycle with inert adapters; never opens hardware or sockets."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from test_project_mcp_integration import integrated  # noqa: F401
from nerv.brain_core.core import BrainCore


def test_two_device_attachments_and_run_end_leave_peer_and_world_alive(integrated, monkeypatch):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    devices = {}
    profiles = {}
    for name in ('device-a', 'device-b'):
        profile = SimpleNamespace(name=name, buses={'real':SimpleNamespace(enabled=True)})
        profile.model_copy = lambda update:SimpleNamespace(name=update['name'])
        profiles[name] = profile
    f.hub.registry.bodies.update(profiles)
    original_body = f.hub.registry.body
    f.hub.registry.body = lambda name:profiles.get(name) or original_body(name)
    def ensure(profile, mode):
        assert mode == 'real'
        device = dict(ready=False, closed=False, epoch=profile.name + '-epoch', completed=0)
        def post(path, payload):
            if path == '/prepare': device['ready'] = True
            if path == '/device/close': device['closed'] = True
            return {'ok':True}
        device['client'] = SimpleNamespace(post=post, health=lambda:{'epoch':device['epoch']})
        devices[profile.name] = device
        f.launcher.nodes['body:' + profile.name] = SimpleNamespace(meta={'epoch':device['epoch']})
    f.launcher.ensure_runtime.side_effect = ensure
    monkeypatch.setattr(f.hub, 'body_client', lambda rid:devices[rid]['client'])
    monkeypatch.setattr(f.hub, 'arm', Mock(return_value={'ok':True}))
    monkeypatch.setattr(f.hub.tasks, 'cancel', Mock(return_value={'ok':True, 'stop_confirmed':True}))
    world = object()
    f.launcher.nodes['shared_world:untouched'] = world
    try:
        projects = [f.projects.store.create(dict(name=n, robot=n, mode='real')) for n in profiles]
        a,b = [core.attachments.connect(p['id'], operator=True) for p in projects]
        assert a['robot_instance_id'] != b['robot_instance_id']
        assert all(r['world_instance_id'] is None for r in (a,b))
        started = [f.projects.create_run(p['id'], operator=True) for p in projects]
        assert len(devices) == 2 and f.launcher.ensure_runtime.call_count == 2
        f.projects.stop_run(projects[0]['id'], started[0]['run']['id'])
        assert not devices[a['robot_instance_id']]['closed'], 'Run end disconnected the device'
        f.projects.require_session(projects[1]['id'])
        after = core.attachments.disconnect(a['robot_instance_id'], operator=True)
        assert after['brain_instance_id'] == a['brain_instance_id'] and after['status'] == 'offline'
        assert devices[a['robot_instance_id']]['closed'] and not devices[b['robot_instance_id']]['closed']
        assert f.launcher.nodes['shared_world:untouched'] is world
        f.launcher.stop.assert_called_once_with('body:' + a['robot_instance_id'])
        # An explicitly invoked inert adapter command still completes on B.
        assert devices[b['robot_instance_id']]['client'].post('/fixture/complete', {})['ok']
        assert core.resources.get(b['robot_instance_id'])['status'] == 'ready'
        f.launcher.ensure_world.assert_not_called()
    finally:
        core.shutdown()


def test_device_attachment_requires_operator_and_rejects_duplicate_profile(integrated, monkeypatch):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        profile=SimpleNamespace(name='registered', buses={'real':SimpleNamespace(enabled=True)},
            model_copy=lambda update:SimpleNamespace(name=update['name']))
        f.hub.registry.body=lambda name:profile
        first=f.projects.store.create(dict(name='First',mode='real',robot=profile.name))
        second=f.projects.store.create(dict(name='Duplicate',mode='real',robot=profile.name))
        with pytest.raises(PermissionError):core.attachments.connect(first['id'])
        assert not f.launcher.ensure_runtime.called
        core.attachments.connect(first['id'],operator=True)
        with pytest.raises(ValueError,match='already attached'):core.attachments.connect(second['id'],operator=True)
        assert f.launcher.ensure_runtime.call_count==1
    finally:core.shutdown()


def test_failed_preparation_requires_confirmed_close_before_reconnect(integrated):
    f = integrated
    core = BrainCore(f.hub, f.projects, f.gw.root)
    try:
        profile = SimpleNamespace(name='registered', buses={'real':SimpleNamespace(enabled=True)},
            model_copy=lambda update:SimpleNamespace(name=update['name']))
        f.hub.registry.body = lambda name:profile
        project = f.projects.store.create(dict(name='Device',mode='real',robot=profile.name))
        f.robot.post.side_effect = lambda *_: {'ok':False}
        with pytest.raises(RuntimeError, match='preparation'):
            core.attachments.connect(project['id'], operator=True)
        failed = core.resources.for_project(project['id'])
        assert failed['runtime_attachment']['status'] == 'close_unconfirmed'
        with pytest.raises(ValueError, match='Confirm device disconnect'):
            core.attachments.connect(project['id'], operator=True)
        with pytest.raises(ValueError, match='disconnect is unconfirmed'):
            core.attachments.disconnect(failed['robot_instance_id'], operator=True)
        f.launcher.stop.assert_not_called()
        f.robot.post.side_effect = lambda *_: {'ok':True}
        core.attachments.disconnect(failed['robot_instance_id'], operator=True)
        ready = core.attachments.connect(project['id'], operator=True)
        assert ready['runtime_generation'] == failed['runtime_generation'] + 1
        assert ready['brain_instance_id'] == failed['brain_instance_id']
        from nerv.brain_core.resources import RobotResources
        recovered = RobotResources(f.projects).get(ready['robot_instance_id'])
        assert recovered['status'] == recovered['runtime_attachment']['status'] == 'offline'
        assert recovered['brain_instance_id'] == ready['brain_instance_id']
    finally:
        core.shutdown()


@pytest.mark.parametrize('method,path', [
    ('post','/api/projects/example/runtime/connect'),
    ('post','/api/robot-instances/robot_example/disconnect'),
    ('get','/api/robot-instances/robot_example/observation'),
])
def test_agent_bearer_cannot_use_operator_attachment_routes(method, path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from nerv.platform.project_api import router
    app = FastAPI()
    app.include_router(router(lambda:pytest.fail('Authorization must precede Host access')))
    with TestClient(app) as client:
        assert client.request(method,path,headers={'Authorization':'Bearer fixture'}).status_code == 403
