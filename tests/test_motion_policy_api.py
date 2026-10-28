"""Motion-policy selection is an operator change, blocked while a robot is bound."""
from types import SimpleNamespace
from threading import RLock

import pytest
from fastapi import HTTPException

from nerv import motion_policies, paths


@pytest.fixture
def server():
    # Import only after the isolated-data fixture has selected the test home.
    from nerv.platform import server as module
    return module


@pytest.fixture
def hub(monkeypatch, server):
    value = SimpleNamespace(_node_lock=RLock(), _bound_runtime=None,
                            registry=SimpleNamespace(bodies={}), launcher=SimpleNamespace())
    monkeypatch.setattr(server, 'nerv', value)
    return value


@pytest.mark.parametrize('operation', ['import', 'check', 'select'])
def test_bound_instance_rejects_policy_mutations(hub, monkeypatch, operation, server):
    hub._bound_runtime = ('robot', 'sim', 'scene')
    monkeypatch.setattr(motion_policies, 'manage', lambda *a: pytest.fail('unexpected mutation'))
    with pytest.raises(HTTPException) as error:
        server.motion_policy_operation(operation, {'id': 'example'})
    assert error.value.status_code == 409


def test_selection_refreshes_registry_without_starting_a_node(hub, monkeypatch, server):
    from nerv.platform import registry
    calls = []
    monkeypatch.setattr(motion_policies, 'manage', lambda *args: calls.append(args) or {'revision': 'fixed'})
    updated = SimpleNamespace()
    monkeypatch.setattr(registry, 'Registry', lambda: updated)
    assert server.motion_policy_operation('select', {'id': 'custom', 'revision': 'fixed'}) == {'revision': 'fixed'}
    assert calls == [(paths.NERV_HOME, 'select', {'id': 'custom', 'revision': 'fixed'}, '')]
    assert hub.registry is updated and hub.launcher.registry is updated


@pytest.mark.parametrize('payload', [{'id': 7}, {'id': 'x', 'command': 'arbitrary'}])
def test_unknown_payload_is_rejected_before_mutation(hub, payload, server):
    with pytest.raises(HTTPException) as error:
        server.motion_policy_operation('select', payload)
    assert error.value.status_code == 400
