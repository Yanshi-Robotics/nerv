"""Real MCP HTTP control metadata, strict business schemas and credential-free logs.

These inert providers exercise transport and counters, never physical devices.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from conftest import free_port, serve
from nerv.platform.client import RemoteBody
from nerv.platform import mcp_bridge
from nerv.platform.launcher import Launcher
from nerv.platform.session import log
from nerv_runtime_sdk.contracts import CONTROL_META_KEY, CONTROL_TRANSPORT
from nerv_runtime_sdk.control import StopFlag
from nerv_runtime_sdk.node import BodyNode, build_app


class StrictCounter:
    family, version = 'inert-metadata-fixture', '1'

    def __init__(self, name):
        self.name, self.held = name, False
        self.calls = []
        self.progress_message = 'halfway'

    def actions(self):
        return [
            dict(name='set_value', kind='primitive', parameters={
                'type':'object', 'properties':{'value':{'type':'number'}},
                'required':['value'], 'additionalProperties':False}),
            dict(name='set_nested', kind='primitive', parameters={
                'allOf':[{'type':'object', 'properties':{'item':{
                    'type':'object', 'properties':{'value':{'type':'integer'}},
                    'required':['value'], 'additionalProperties':False}},
                    'required':['item'], 'additionalProperties':False}]}),
            dict(name='read_count', kind='read', parameters={'type':'object','properties':{}}),
        ]

    def invoke(self, name, *, _progress=None, **kwargs):
        if name == 'read_count':
            return dict(ok=True, data={'completed':len(self.calls)})
        self.calls.append((name, kwargs))
        if _progress:
            _progress(.5, self.progress_message)
        return dict(ok=True, message=self.progress_message, data={'completed':len(self.calls)})

    def observe(self): return self.status(), []
    def status(self): return dict(completed=len(self.calls), held=self.held, fixture_only=True)
    def sensor_names(self): return []
    def config_options(self): return []
    def stream_jpeg(self): return None
    def set_option(self, *_): return {'ok':False}
    def hold(self, *_):
        self.held = True
        return {'ok':True}
    def release(self):
        self.held = False
        return {'ok':True}
    def close(self): pass


@contextmanager
def endpoint(name='strict-counter'):
    impl = StrictCounter(name)
    node = BodyNode(impl, guidance='Inert metadata fixture', stop=StopFlag(), armed=True, require_control=True)
    port = free_port()
    server, thread = serve(build_app(node, []), port)
    url = f'http://127.0.0.1:{port}'
    try:
        yield SimpleNamespace(impl=impl, node=node, url=url, body=RemoteBody(name, url))
    finally:
        server.should_exit = True
        thread.join(3)
        assert not thread.is_alive()


def claim(endpoint, token, *, generation=1):
    response = httpx.post(endpoint.url+'/control/claim', json=dict(
        owner='inert-controller', generation=generation, token=token,
        allowed_actions=['set_value','set_nested'], max_runtime_s=30, action_budget=5))
    assert response.json()['ok']


def direct(endpoint, name, arguments, metadata=None):
    async def operation(session):
        return await session.call_tool(name, arguments, meta=metadata)
    return mcp_bridge.run_sync(mcp_bridge.with_session(endpoint.url+'/mcp/', operation, 3), 5)


def test_strict_schema_control_progress_and_logs_use_separate_metadata(tmp_path):
    sentinel = 'fixture-runtime-control-never-in-logs'
    first_id = max((x['id'] for x in log.recent_ring()), default=0)
    with endpoint() as e:
        claim(e, sentinel)
        assert e.body.health()['control_transport'] == CONTROL_TRANSPORT
        caps = e.body.capabilities()
        assert next(a.parameters for a in caps.operations if a.name=='set_value') == e.impl.actions()[0]['parameters']
        e.impl.progress_message = 'progress ' + sentinel
        progress = []
        with log.session_scope('strict-metadata'):
            result = e.body.invoke('set_value', value=12, _control_token=sentinel,
                _on_progress=lambda message, *_:progress.append(message))
        assert result.ok and result.data['completed'] == 1
        assert e.impl.calls == [('set_value', {'value':12})]
        assert e.node._action_budget == 4
        assert progress and all(sentinel not in message for message in progress)
        assert sentinel not in result.message
        assert direct(e, 'read_count', {}).structuredContent == {'completed':1}
    entries = log.recent_ring(first_id)
    assert any(x.get('method')=='progress' for x in entries)
    assert sentinel not in json.dumps(entries)
    files = list((tmp_path/'logs/sessions').glob('*.jsonl'))
    assert files and all(sentinel not in p.read_text() for p in files)


@pytest.mark.parametrize('name,arguments', [
    ('set_value', {'value':1, 'extra':2}),
    ('set_value', {}),
    ('set_value', {'value':'wrong type'}),
    ('set_nested', {'item':{'value':1, 'extra':2}}),
])
def test_business_schema_rejection_preserves_dispatch_budget(name, arguments):
    with endpoint() as e:
        claim(e, 'inert-good')
        result = direct(e, name, arguments, {CONTROL_META_KEY:{'token':'inert-good'}})
        assert result.isError
        assert e.impl.calls == [] and e.node._action_budget == 5


def test_nested_allof_strict_schema_accepts_metadata_without_mutating_schema():
    with endpoint() as e:
        claim(e, 'inert-good')
        original = e.impl.actions()[1]['parameters']
        result = direct(e, 'set_nested', {'item':{'value':4}}, {CONTROL_META_KEY:{'token':'inert-good'}})
        assert not result.isError
        assert e.impl.calls == [('set_nested', {'item':{'value':4}})]
        assert next(a.parameters for a in e.body.capabilities().operations if a.name=='set_nested') == original


@pytest.mark.parametrize('metadata', [
    None, {CONTROL_META_KEY:{'token':'wrong'}}, {CONTROL_META_KEY:None},
    {CONTROL_META_KEY:'inert-good'}, {CONTROL_META_KEY:{'token':''}},
    {CONTROL_META_KEY:{'token':'inert-good','extra':True}},
])
def test_missing_wrong_or_malformed_metadata_cannot_dispatch(metadata):
    with endpoint() as e:
        claim(e, 'inert-good')
        result = direct(e, 'set_value', {'value':1}, metadata)
        assert result.isError
        assert e.impl.calls == [] and e.node._action_budget == 5


@pytest.mark.parametrize('metadata', [None, {CONTROL_META_KEY:{'token':'wrong'}}, {CONTROL_META_KEY:{'token':'inert-good'}}])
def test_legacy_argument_and_dual_source_credentials_are_rejected(metadata):
    with endpoint() as e:
        claim(e, 'inert-good')
        # This read schema accepts extra keys, so the SDK handler itself must
        # refuse the legacy credential; there is no fallback on wrong metadata.
        result = direct(e, 'read_count', {'_nerv_control':'inert-good'}, metadata)
        assert result.isError
        assert e.impl.calls == [] and e.node._action_budget == 5


@pytest.mark.parametrize('reason', ['expired', 'revoked'])
def test_stale_metadata_cannot_dispatch(reason):
    with endpoint() as e:
        claim(e, 'inert-good')
        if reason == 'expired':
            e.node._control_deadline = time.monotonic() - 1
        else:
            httpx.post(e.url+'/stop', json={}).raise_for_status()
        result = direct(e, 'set_value', {'value':1}, {CONTROL_META_KEY:{'token':'inert-good'}})
        assert result.isError
        assert e.impl.calls == [] and e.node._action_budget == 5


def test_two_concurrent_endpoints_keep_request_metadata_and_budgets_separate():
    with endpoint('counter-A') as a, endpoint('counter-B') as b:
        claim(a, 'inert-A')
        claim(b, 'inert-B')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(e.body.invoke, 'set_value', value=value, _control_token=token)
                for e,value,token in [(a,1,'inert-A'),(b,2,'inert-B')]]
            assert all(f.result().ok for f in results)
        assert a.impl.calls == [('set_value',{'value':1})]
        assert b.impl.calls == [('set_value',{'value':2})]
        for e,other in [(a,'inert-B'),(b,'inert-A')]:
            assert not e.body.invoke('set_value', value=3, _control_token=other).ok
            assert e.node._action_budget == 4 and len(e.impl.calls)==1


def test_launcher_rejects_legacy_runtime_before_preparation():
    with pytest.raises(ValueError, match='metadata'):
        Launcher._runtime_protocol(SimpleNamespace(meta={'protocol_major':3}))
    Launcher._runtime_protocol(SimpleNamespace(meta={'protocol_major':3,'control_transport':CONTROL_TRANSPORT}))
