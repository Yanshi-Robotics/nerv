"""Simulation-only supervision receipts and local watchdog failure behavior."""
from contextlib import nullcontext
from types import SimpleNamespace
import threading
import time

import pytest

from nerv.body.skills import SkillRunner, StopFlag
from nerv.runtime.builtin import BuiltinRuntime


def runtime():
    profile = SimpleNamespace(id='fixture', family='arm', configuration={'buses': {'sim': {'settings': {
        'external_supervision': {'timeout_s': .1, 'max_observation_age_s': .05, 'poll_s': .005}}}}})
    value = BuiltinRuntime(profile, SimpleNamespace(configuration={}))
    value.stop = StopFlag()
    runner = SkillRunner(value.stop)
    value._impl = SimpleNamespace(runner=runner, observe=lambda: ({}, [('camera', b'fixture')]),
                                  status=lambda: {}, close=lambda: None, hold=lambda reason: {'ok':True,'stop_confirmed':True})
    return value


def test_only_fresh_complete_observations_renew_supervision():
    value = runtime()
    try:
        assert value.supervise(1)['ok'] is False
        state, _ = value.observe()
        assert value.supervise(state['frame_seq'])['ok']
        assert value.supervise(state['frame_seq'])['ok'] is False
        assert value.supervise(True)['ok'] is False
        value._impl.observe = lambda: ({'sensor_errors': {'camera': 'missing'}}, [])
        state, _ = value.observe()
        assert value.supervise(state['frame_seq'])['ok'] is False
        state, _ = value._record_observation(time.monotonic() - 1, {}, [('camera', b'fixture')])
        assert value.supervise(state['frame_seq'])['ok'] is False
    finally:
        value.close()


def test_watchdog_stops_active_runner_and_blocks_late_dispatch():
    value = runtime()
    began = threading.Event()
    out = {}
    def action(progress, aborted):
        began.set()
        deadline = time.monotonic() + 2
        while not aborted() and time.monotonic() < deadline:
            time.sleep(.001)
        return {'ok': False, 'data': {'stopped': aborted()}}
    value._impl.invoke = lambda name, **kw: value._impl.runner.run(name, action)
    try:
        state, _ = value.observe()
        assert value.supervise(state['frame_seq'])['ok']
        worker = threading.Thread(target=lambda: out.update(value.invoke('move', _control_guard=nullcontext)))
        worker.start()
        assert began.wait(1)
        worker.join(1)
        assert not worker.is_alive() and out['data']['stopped']
        assert value.status()['external_supervision']['expired']
        with pytest.raises(InterruptedError, match='supervision expired'):
            value.invoke('move')
        state, _ = value.observe()
        assert value.supervise(state['frame_seq'])['ok']
        assert not value.status()['external_supervision']['expired']
    finally:
        value.close()


def test_profile_missing_does_not_claim_supervision_support():
    value = BuiltinRuntime(SimpleNamespace(id='fixture', family='arm', configuration={}), None)
    assert value.external_supervision_config()['validated'] is False
    assert value.supervise(1)['ok'] is False
    value.close()


def test_close_joins_owned_watchdog():
    value = runtime()
    state, _ = value.observe()
    assert value.supervise(state['frame_seq'])['ok']
    value.close()
    assert not value._supervision_thread.is_alive()
    assert value.supervise(state['frame_seq'] + 1)['ok'] is False


def test_cancel_invalidates_receipts_and_allows_manual_dispatch():
    value = runtime()
    value._impl.invoke = lambda name, **kw: value._impl.runner.run(
        name, lambda progress, aborted: {'ok': True})
    try:
        state, _ = value.observe()
        assert value.supervise(state['frame_seq'])['ok']
        unacknowledged, _ = value.observe()
        with value._supervision_lock:
            value._supervision_expired = True
        assert value.cancel_current()['ok']
        assert value.status()['external_supervision']['active'] is False
        assert value.invoke('manual')['ok']
        assert value.supervise(state['frame_seq'])['ok'] is False
        assert value.supervise(unacknowledged['frame_seq'])['ok'] is False
        fresh, _ = value.observe()
        assert value.supervise(fresh['frame_seq'])['ok']
    finally:
        value.close()


def test_node_cancel_revokes_old_token_before_resetting_supervision():
    from nerv_runtime_sdk.node import BodyNode
    value = runtime()
    value._impl.tools = lambda: [{'name': 'move', 'kind': 'primitive'}]
    value._impl.config_options = lambda: []
    value._impl.held = False
    value._impl.invoke = lambda name, **kw: value._impl.runner.run(
        name, lambda progress, aborted: {'ok': True})
    node = BodyNode(value, guidance='', stop=value.stop, armed=True, require_control=True)
    node._control_token = 'old-grant'
    node._control_deadline = time.monotonic() + 10
    node._allowed_actions = frozenset({'move'})
    node._action_budget = 2
    try:
        assert node.config()['external_supervision']['scope'] == 'simulation-software'
        frame, _ = value.observe()
        assert value.supervise(frame['frame_seq'])['ok']
        assert node.cancel()['ok']
        with pytest.raises(InterruptedError, match='stale or missing control'):
            node.invoke('move', _nerv_control='old-grant')
        assert not value.supervise(frame['frame_seq'])['ok']
    finally:
        value.close()


def test_cancel_propagates_physical_stop_failure():
    value = runtime()
    value._impl.hold = lambda reason: {'ok':False, 'stop_confirmed':False, 'stop_state':'stop_unconfirmed'}
    try:
        result=value.cancel_current()
        assert result['accepted'] and result['dispatch_revoked']
        assert result['ok'] is False and result['stop_confirmed'] is False
    finally:
        value.close()
