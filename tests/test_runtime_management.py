"""Host/provider boundary and manual sessions; no devices or model requests."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nerv.platform.hub import Nerv
from nerv.platform.session import SessionStore


def test_manual_session_needs_no_model_credentials(tmp_path, monkeypatch):
    from nerv.vlm.store import ModelRegistry
    require = Mock(side_effect=AssertionError("manual mode must not query credentials"))
    monkeypatch.setattr(ModelRegistry, "require_key", require)
    hub = Nerv(store=SessionStore(str(tmp_path)))
    session = hub.new_session("operator", None, None, tools=[])
    assert session["brain"] == "operator"
    assert not session["armed"]
    require.assert_not_called()
    with pytest.raises(ValueError, match="visual model"):
        hub.new_role_model("operator")


def test_provider_description_runs_in_selected_environment(tmp_path, monkeypatch):
    from nerv import paths
    from nerv.platform.runtime_management import manage
    monkeypatch.setattr(paths, "NERV_HOME", str(tmp_path))
    result = manage(sys.executable, "lerobot0.6.1", "describe", {})
    assert result["product"] == "so101-standard-v1"
    fields = result["operations"]["configure"]["properties"]
    assert "calibration_dir" in fields
    assert "joint_mapping" not in fields
    assert not list(tmp_path.rglob("*.json"))


def test_manual_dispatch_observes_before_motion(tmp_path, monkeypatch):
    hub = Nerv(store=SessionStore(str(tmp_path)))
    session, _ = hub.store.new("operator", "fixture", None)
    order = []
    turn = SimpleNamespace(tools=lambda: None, _routes={"move": ("body", None, SimpleNamespace(kind="primitive"))},
                           session=session, observe=lambda: order.append("observe"),
                           think=lambda _: order.append("think"), call_tool=lambda _: order.append("call"))
    monkeypatch.setattr(hub, "_turn", lambda *a, **k: turn)
    monkeypatch.setattr(hub, "_epoch_ok", lambda s: True)
    monkeypatch.setattr(hub.tasks, "cancel", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(hub, "claim_robot", lambda sid: "token")
    list(hub.teleop_stream(session.id, "move", {}))
    assert order == ["observe", "think", "call"]


def test_manual_dispatch_never_calls_motion_without_observation(tmp_path, monkeypatch):
    hub = Nerv(store=SessionStore(str(tmp_path)))
    session, _ = hub.store.new("operator", "fixture", None)
    def fail():
        raise RuntimeError("camera lost")
    call = Mock()
    turn = SimpleNamespace(tools=lambda: None, _routes={"move": ("body", None, SimpleNamespace(kind="primitive"))},
                           session=session, observe=fail, think=Mock(), call_tool=call)
    monkeypatch.setattr(hub, "_turn", lambda *a, **k: turn)
    monkeypatch.setattr(hub, "_epoch_ok", lambda s: True)
    monkeypatch.setattr(hub.tasks, "cancel", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(hub, "claim_robot", lambda sid: "token")
    events = list(hub.teleop_stream(session.id, "move", {}))
    call.assert_not_called()
    assert any(e.get("ok") is False and "camera lost" in e.get("message", "") for e in events)
