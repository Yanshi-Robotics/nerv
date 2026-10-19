"""Runtime protocol fixtures are software tests, not robot demonstrations."""
import subprocess
import sys
import threading
import time

import pytest

from nerv_runtime_sdk.contracts import RuntimeManifest
from nerv_runtime_sdk.control import DispatchAuthority, ManagedExecutions
from nerv.platform.registry.schema import BodySpec


def test_dispatch_generation_rejects_late_action():
    dispatch = DispatchAuthority()
    dispatch.enable(True)
    token = dispatch.token()
    dispatch.revoke()
    sent = []
    with pytest.raises(InterruptedError):
        dispatch.write(token, lambda: sent.append("late"))
    assert sent == []


def test_cancel_during_inference_stops_dispatch_and_holds_once():
    dispatch = DispatchAuthority()
    dispatch.enable(True)
    manager = ManagedExecutions(dispatch, supervision_timeout_s=1, cancel_timeout_s=0.01, poll_s=0.01)
    entered, finish = threading.Event(), threading.Event()
    writes = []
    manager.on_stop = lambda: writes.append("hold")

    def operation(token, _stop):
        entered.set()
        assert finish.wait(2)
        dispatch.write(token, lambda: writes.append("late-policy-action"))

    state = manager.start("fixture", operation)
    assert entered.wait(1)
    assert manager.cancel(state["execution_id"])["dispatch_stopped"]
    with pytest.raises(RuntimeError, match="another execution"):
        manager.start("second", operation)
    finish.set()
    manager.wait(state["execution_id"], 1)
    assert writes == ["hold"]


def test_preparation_cannot_start_after_cancel():
    dispatch = DispatchAuthority()
    dispatch.enable(True)
    token = dispatch.token()
    dispatch.revoke()
    manager = ManagedExecutions(dispatch, supervision_timeout_s=1, cancel_timeout_s=0.01, poll_s=0.01)
    with pytest.raises(InterruptedError):
        manager.start("stale-start", lambda *_: {}, expected_token=token)


def test_watchdog_terminates_without_cloud_or_camera_requests():
    dispatch = DispatchAuthority()
    dispatch.enable(True)
    manager = ManagedExecutions(dispatch, supervision_timeout_s=0.04, cancel_timeout_s=0.02, poll_s=0.005)
    manager.start("fixture", lambda token, stop: stop.wait(1))
    deadline = time.monotonic() + 1
    while manager.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.status()["stop_reason"] == "supervision_lost"
    assert not manager.status()["running"]


def test_provider_discovery_imports_no_native_robot_or_tensor_sdk():
    code = "from nerv_runtime_sdk.worker import discover; import sys; print(discover()); assert 'lerobot' not in sys.modules; assert 'torch' not in sys.modules; assert 'mujoco' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "lerobot0.6.1" in result.stdout


def test_external_family_and_protocol_negotiation():
    profile = BodySpec(name="external", family="warehouse-cart", buses={"real": {"kind": "vendor", "provider": "external"}})
    assert profile.family == "warehouse-cart"
    with pytest.raises(ValueError, match="incompatible"):
        RuntimeManifest(id="bad", version="1", protocol_major=99).check_protocol()
