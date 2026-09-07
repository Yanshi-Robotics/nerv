"""Changing maps must not silently reuse a body attached to another world."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nerv.platform.hub import Nerv
from nerv.platform.launcher import NodeHandle
from nerv.platform.session import SessionStore


@pytest.fixture
def hub(tmp_path, monkeypatch):
    value = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    # These tests never start processes or connect to a resident service.
    monkeypatch.setattr(value.launcher, "_spawn", Mock(side_effect=AssertionError("unexpected launch")))
    return value


def live_body(hub, world="apt"):
    body = hub.registry.body("humanoid-unitree-g1")
    handle = NodeHandle("body", f"body:{body.name}", "http://unused",
                        proc=SimpleNamespace(poll=lambda: None),
                        meta={"world": world})
    hub.launcher.nodes[handle.name] = handle
    return body, handle


def test_same_world_reuses_body(hub):
    body, handle = live_body(hub)
    assert hub.launcher.ensure_body(body, hub.registry.world("apt"), None) is handle


def test_different_world_refused_before_launch_or_session_changes(hub, monkeypatch):
    body, handle = live_body(hub)
    old, _ = hub.store.new("mock", body.name, "apt", tools=[], epoch="old-epoch")
    before = old.summary()
    ensure_world = Mock(side_effect=AssertionError("world must not start"))
    monkeypatch.setattr(hub.launcher, "ensure_world", ensure_world)
    with pytest.raises(ValueError, match="cannot reuse"):
        hub.new_session("mock", body.name, "house", tools=[])
    ensure_world.assert_not_called()
    assert hub.store.get(old.id).summary() == before
    assert len(hub.store.list()) == 1
    assert hub.launcher.nodes[handle.name] is handle


def test_direct_body_reuse_also_refuses_mismatch(hub):
    body, _ = live_body(hub)
    with pytest.raises(ValueError, match="cannot reuse"):
        hub.launcher.ensure_body(body, hub.registry.world("house"), None)


def test_missing_world_probes_health_and_refuses_unknown(hub, monkeypatch):
    body, handle = live_body(hub, "")
    probe = Mock(return_value={})
    monkeypatch.setattr(hub.launcher, "_wait_health", probe)
    with pytest.raises(ValueError, match="unknown"):
        hub.launcher.check_body_binding(body, hub.registry.world("apt"))
    probe.assert_called_once_with(handle)
    def health(node):
        node.meta = {"world": "apt"}
        return node.meta
    monkeypatch.setattr(hub.launcher, "_wait_health", health)
    hub.launcher.check_body_binding(body, hub.registry.world("apt"))


def test_dead_body_does_not_block_a_new_world(hub):
    body, handle = live_body(hub)
    handle.attached = False
    handle.proc = SimpleNamespace(poll=lambda: 0)
    hub.launcher.check_body_binding(body, hub.registry.world("house"))


def test_same_world_new_bus_cannot_reuse_old_body(hub):
    body, handle = live_body(hub)
    handle.bus_url = "tcp://old"
    world_handle = NodeHandle("world", "apt/g1", "http://unused", bus_url="tcp://new")
    with pytest.raises(ValueError, match="earlier world bus"):
        hub.launcher.ensure_body(body, hub.registry.world("apt"), world_handle)


def test_attached_endpoint_checked_before_world_launch(hub, monkeypatch):
    body = hub.registry.body("humanoid-unitree-g1")
    monkeypatch.setattr(body, "url", "http://attached")
    def health(node):
        node.meta = {"world": "house"}
        return node.meta
    monkeypatch.setattr(hub.launcher, "_wait_health", health)
    with pytest.raises(ValueError, match="cannot reuse"):
        hub.new_session("mock", body.name, "apt", tools=[])
    assert hub.store.list() == []
    assert hub.launcher.nodes == {}


def test_conversation_only_ignores_body_binding(hub):
    live_body(hub)
    session = hub.new_session("mock", None, None, tools=[])
    assert session["world"] is None and session["body"] is None


def test_attached_node_rebound_at_same_url_is_not_trusted(hub, monkeypatch):
    body, handle = live_body(hub)
    handle.attached = True
    def health(node):
        node.meta = {"world": "house"}
        return node.meta
    monkeypatch.setattr(hub.launcher, "_wait_health", health)
    with pytest.raises(ValueError, match="cannot reuse"):
        hub.new_session("mock", body.name, "apt", tools=[])
    assert hub.store.list() == []
