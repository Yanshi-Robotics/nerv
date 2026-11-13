"""Concurrent stop authorities must share results without crossing generations."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading
from unittest.mock import Mock

import pytest
from test_control_schemes import node_fixture


@pytest.mark.parametrize('outcome', ['confirmed', 'unconfirmed', 'exception'])
def test_same_generation_stops_share_provider_result_and_revoke_dispatch(outcome):
    node, client = node_fixture()
    grant = dict(token='one', owner='host', generation=1, allowed_actions=['pick'], max_runtime_s=60, action_budget=1)
    assert client.post('/control/claim', json=grant).json()['ok']
    entered, release = threading.Event(), threading.Event()
    calls = []
    def provider(reason):
        calls.append(reason)
        entered.set()
        assert release.wait(2)
        if outcome == 'exception':
            raise RuntimeError('fixture provider failure')
        return dict(ok=outcome == 'confirmed', stop_confirmed=outcome == 'confirmed',
                    stop_state='stopped' if outcome == 'confirmed' else 'stop_unconfirmed', revision=7)
    node.impl.cancel_current = provider
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(node.cancel, 'control_grant_expired', expected='one')
        try:
            assert entered.wait(1)
            with pytest.raises(InterruptedError):
                with node.control_guard('one'):
                    pass
            joiners = [pool.submit(node.cancel), pool.submit(node.cancel, expected='one')]
            for future in joiners:
                with pytest.raises(TimeoutError):
                    future.result(.05)
            assert not client.post('/control/claim', json={**grant, 'token':'two', 'generation':2}).json()['ok']
        finally:
            release.set()
        results = [first.result(1), *[future.result(1) for future in joiners]]
    assert len(calls) == 1
    assert all(result == results[0] for result in results)
    assert all(result['ok'] is (outcome == 'confirmed') for result in results)
    assert results[0]['stop_operation_id']
    assert client.post('/control/claim', json={**grant, 'token':'two', 'generation':2}).json()['ok']
    assert not node.cancel(expected='one')['ok']
    with node.control_guard('two'):
        pass


@pytest.mark.parametrize('require_control', [True, False])
def test_join_timeout_never_confirms_stop_or_unblocks_new_claim(require_control):
    node, client = node_fixture()
    node.require_control = require_control
    node.stop_join_timeout_s = .01
    grant = dict(token='one', owner='host', generation=1, allowed_actions=['pick'], max_runtime_s=60, action_budget=1)
    assert client.post('/control/claim', json=grant).json()['ok']
    entered, release = threading.Event(), threading.Event()
    def provider(_):
        entered.set()
        assert release.wait(2)
        return dict(ok=True, stop_confirmed=True)
    node.impl.cancel_current = provider
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(node.cancel)
        try:
            assert entered.wait(1)
            result = node.cancel()
            assert not result['ok'] and result['stop_confirmed'] is False
            assert result['stop_state'] == 'stopping'
            assert result['dispatch_revoked'] is require_control
            assert not client.post('/control/claim', json={**grant,'token':'two','generation':2}).json()['ok']
        finally:
            release.set()
        assert first.result(1)['ok']


def test_completed_stop_is_not_reused_as_current_physical_evidence():
    node, _ = node_fixture()
    node.impl.cancel_current.side_effect = [dict(ok=True, stop_confirmed=True), dict(ok=False, stop_confirmed=False)]
    assert node.cancel()['ok']
    assert not node.cancel()['ok']
    assert node.impl.cancel_current.call_count == 2


def test_stop_transport_outlasts_runtime_join_without_relaxing_other_requests(monkeypatch):
    from nerv import config
    from nerv.platform.client import RemoteBody
    monkeypatch.setattr(config, 'NODE_TIMEOUT', 30)
    monkeypatch.setattr(config, 'NODE_STATUS_TIMEOUT', 5)
    body = RemoteBody('fixture', 'http://unused.invalid')
    body._http.close()
    body._http = Mock()
    body._http.post.return_value.json.return_value = {'ok': True}
    body.post('/stop')
    assert body._http.post.call_args.kwargs['timeout'] == 35
    body.post('/executions/status')
    assert body._http.post.call_args.kwargs['timeout'] == 5
