"""Independent Hosts must not accept or allocate each other's node endpoints."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import sys

import httpx
import pytest

from nerv import config, paths
from nerv.paths import node_coordination_dir
from nerv.platform.launcher import Launcher, NodeHandle


@pytest.fixture
def launchers(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'LOGS_DIR', str(tmp_path / 'logs'))
    monkeypatch.setattr(paths, 'node_coordination_dir', lambda: str(tmp_path / 'coordination'))
    return Launcher(None), Launcher(None)


def test_two_hosts_serialize_port_choice_until_child_is_ready(launchers, monkeypatch):
    first, second = launchers
    first_started, finish_first, second_attempted = (threading.Event() for _ in range(3))
    second_allocated = threading.Event()
    occupied = set()
    tool = SimpleNamespace(name='fixture', url='', python=__file__, module='fixture')

    def allocate(which):
        if which is second:
            second_allocated.set()
        return next(p for p in (1, 2) if p not in occupied)

    def spawn(which, key, kind, python, args, url, **kwargs):
        port = int(url.rsplit(':', 1)[1])
        if which is first:
            first_started.set()
            assert finish_first.wait(3)
        assert port not in occupied
        occupied.add(port)  # Model the child's listening socket becoming ready.
        return NodeHandle(kind, key, url, attached=True)

    for launcher in launchers:
        monkeypatch.setattr(launcher, '_free_port', lambda owner=launcher: allocate(owner))
        monkeypatch.setattr(launcher, '_spawn', lambda *a, owner=launcher, **kw: spawn(owner, *a, **kw))
    def start_second():
        second_attempted.set()
        return second.ensure_tool(tool)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(first.ensure_tool, tool)
        assert first_started.wait(1)
        following = pool.submit(start_second)
        assert second_attempted.wait(1)
        try:
            assert not second_allocated.wait(.1)
        finally:
            finish_first.set()
        assert pending.result().url != following.result().url
    assert occupied == {1, 2}


def test_allocation_lock_releases_after_startup_failure(launchers, monkeypatch):
    first, second = launchers
    tool = SimpleNamespace(name='fixture', url='', python=__file__, module='fixture')
    monkeypatch.setattr(first, '_free_port', lambda: 1)
    monkeypatch.setattr(first, '_spawn', lambda *a, **kw: (_ for _ in ()).throw(ValueError('failed startup')))
    with pytest.raises(ValueError, match='failed startup'):
        first.ensure_tool(tool)
    with second._allocation_lock.acquire(timeout=0):
        pass


@pytest.mark.parametrize('foreign', [{}, {'launch_token': 'other'}])
def test_foreign_health_is_never_accepted(launchers, monkeypatch, foreign):
    launcher, _ = launchers
    clock = iter([0, 0, 2])
    monkeypatch.setattr(config, 'NODE_HEALTH_WAIT_S', 1)
    monkeypatch.setattr('nerv.platform.launcher.time.monotonic', lambda: next(clock))
    monkeypatch.setattr('nerv.platform.launcher.time.sleep', lambda _: None)
    monkeypatch.setattr('nerv.platform.launcher.httpx.get', lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: foreign))
    handle = NodeHandle('world', 'fixture', 'http://unused', launch_token='expected')
    with pytest.raises(TimeoutError, match='another launch'):
        launcher._wait_health(handle)
    assert not handle.meta


def test_matching_launch_health_and_external_attachment(launchers, monkeypatch):
    launcher, _ = launchers
    reply = {'ok': True, 'launch_token': 'expected'}
    monkeypatch.setattr('nerv.platform.launcher.httpx.get', lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: reply))
    assert launcher._wait_health(NodeHandle('tool', 'fixture', 'http://unused', launch_token='expected')) == reply
    reply = {'ok': True, 'node': 'tool'}
    assert launcher._wait_health(NodeHandle('tool', 'external', 'http://unused', attached=True)) == reply


def test_dead_child_cannot_be_replaced_by_foreign_health(launchers, monkeypatch):
    launcher, _ = launchers
    monkeypatch.setattr('nerv.platform.launcher.httpx.get', lambda *a, **kw: pytest.fail('must check owned process first'))
    handle = NodeHandle('tool', 'fixture', 'http://unused', proc=SimpleNamespace(poll=lambda: 1, returncode=1))
    with pytest.raises(RuntimeError, match='exited with code 1'):
        launcher._wait_health(handle)


def test_coordination_path_is_independent_of_channel_home(monkeypatch):
    before = node_coordination_dir()
    monkeypatch.setenv('NERV_HOME', '/different/channel')
    monkeypatch.setattr(paths, 'NERV_HOME', '/another/channel')
    assert node_coordination_dir() == before


def test_all_local_node_health_routes_report_launch_identity(monkeypatch):
    from nerv.tool.node import build_tool_app
    from nerv.world.mujoco_node import build_app as world_app
    from nerv_runtime_sdk.node import BodyNode, build_app as body_app
    monkeypatch.setenv('NERV_LAUNCH_TOKEN', 'isolated-fixture-launch')
    impl = SimpleNamespace(name='fixture', family='fixture', version='1', held=False, actions=lambda: [])
    body = BodyNode(impl, guidance='', stop=None, armed=False)
    world = SimpleNamespace(world_name='fixture', body_name='fixture',
        phys={'stream_fps': 1, 'jpeg_quality': 75, 'realtime_factor': 1},
        physics_alive=lambda: True, epoch='world-epoch', data=SimpleNamespace(time=0),
        realtime_ratio=1, spawned=False)
    apps = [build_tool_app('fixture', [], lambda *args: {}), body_app(body, []), world_app(world, [])]
    for app in apps:
        endpoint = next(route.endpoint for route in app.routes if route.path == '/health')
        assert endpoint()['launch_token'] == 'isolated-fixture-launch'


@pytest.mark.parametrize('failure', ['http', 'transport', 'json', 'refused', 'protocol'])
def test_failed_runtime_preparation_is_actionable_and_stops_owned_child(launchers, monkeypatch, tmp_path, failure):
    from nerv.platform.registry.schema import BodySpec
    from nerv_runtime_sdk.contracts import CONTROL_TRANSPORT, PROTOCOL_MAJOR
    launcher, _ = launchers
    monkeypatch.setattr(paths, 'CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(launcher, '_free_port', lambda: 1)
    body = BodySpec(name='fixture', family='fixture', buses={'sim': {'kind': 'zmq', 'verified': True, 'python': sys.executable}})
    handle = NodeHandle('body', 'body:fixture', 'http://unused', meta={'protocol_major': PROTOCOL_MAJOR,
        'control_transport': CONTROL_TRANSPORT})
    if failure == 'protocol':
        handle.meta = {}
    def spawn(*args, **kwargs):
        launcher.nodes[handle.name] = handle
        return handle
    stopped = []
    def stop(key):
        stopped.append(key)
        launcher.nodes.pop(key)
    monkeypatch.setattr(launcher, '_spawn', spawn)
    monkeypatch.setattr(launcher, 'stop', stop)
    def prepare(*args, **kwargs):
        request = httpx.Request('POST', 'http://unused/prepare')
        if failure == 'transport':
            raise httpx.ReadTimeout('did not respond', request=request)
        if failure == 'http':
            return httpx.Response(500, request=request)
        if failure == 'json':
            return httpx.Response(200, text='invalid', request=request)
        return httpx.Response(200, json={'ok': False, 'message': 'fixture not ready'}, request=request)
    monkeypatch.setattr('nerv.platform.launcher.httpx.post', prepare)
    with pytest.raises((RuntimeError, ValueError)) as error:
        launcher.ensure_runtime(body, 'sim', world_handle=SimpleNamespace(bus_url='tcp://unused'))
    assert 'protocol' in str(error.value) if failure == 'protocol' else 'preparation' in str(error.value)
    assert stopped == ['body:fixture'] and not launcher.nodes
    # The failure also relinquishes allocation ownership for the other Host.
    with launchers[1]._allocation_lock.acquire(timeout=0):
        pass


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('failure', ['health', 'metadata'])
def test_rejected_external_runtime_never_becomes_a_cached_handshake_bypass(launchers, monkeypatch, cached, failure):
    from nerv.platform.registry.schema import BodySpec
    from nerv_runtime_sdk.contracts import CONTROL_TRANSPORT, PROTOCOL_MAJOR
    launcher, _ = launchers
    body = BodySpec(name='external-fixture', family='fixture', buses={'real':{
        'kind':'inert-network', 'verified':True, 'url':'http://external.invalid'}})
    key = 'body:' + body.name
    if cached:
        launcher.nodes[key] = NodeHandle('body', key, 'http://external.invalid', attached=True,
            meta={'protocol_major':PROTOCOL_MAJOR, 'control_transport':CONTROL_TRANSPORT})
    checks, stops = [], []
    def health(handle):
        checks.append(handle)
        if failure == 'health':
            raise RuntimeError('Fixture health unavailable')
        handle.meta = {'protocol_major':PROTOCOL_MAJOR}  # Real endpoint now has the old contract.
    monkeypatch.setattr(launcher, '_wait_health', health)
    monkeypatch.setattr(launcher, 'stop', lambda key:stops.append(key))
    for _ in range(2):
        with pytest.raises((RuntimeError, ValueError)):
            launcher.ensure_runtime(body, 'real')
        assert key not in launcher.nodes
    assert len(checks) == 2 and not stops
