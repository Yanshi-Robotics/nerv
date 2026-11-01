"""Adapter software receipt checks with inert devices; no hardware acceptance."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from nerv_runtime_lerobot.runtime import LeRobotRuntime
import nerv_runtime_lerobot
from nerv_runtime_sdk.node import BodyNode, build_app
from nerv_runtime_sdk.control import StopFlag
from fastapi.testclient import TestClient


def make_runtime(**overrides):
    product = json.loads((Path(nerv_runtime_lerobot.__file__).parent / "product.json").read_text())
    settings = {**product["buses"]["real"]["settings"], **overrides}
    runtime = LeRobotRuntime(SimpleNamespace(id=product["name"], family=product["family"], configuration={"skills": {}}),
                            SimpleNamespace(configuration=settings))
    runtime._device.prepare = Mock(side_effect=AssertionError("Hardware preparation is forbidden"))
    runtime._robot = SimpleNamespace(cameras={"top": object(), "wrist": object()},
                                     send_action=Mock(side_effect=AssertionError("Hardware writes are forbidden")))
    runtime.execution.on_stop = Mock()
    def capture():
        return {"top": np.zeros((4, 4, 3), dtype=np.uint8), "wrist": np.zeros((4, 4, 3), dtype=np.uint8),
                "joint.pos": 1.0}, {name: time.perf_counter() for name in ("top", "wrist")}
    runtime._capture = capture
    return runtime


def test_binding_contract_is_exposed_without_connecting_or_claiming_validation():
    runtime = make_runtime()
    node = BodyNode(runtime, guidance="", stop=StopFlag(), armed=False, require_control=True)
    response = TestClient(build_app(node, [])).get("/config").json()["external_supervision"]
    assert response["timeout_s"] == runtime.settings["supervision_timeout_s"]
    assert response["max_observation_age_s"] == min(runtime.settings["decision_max_age_s"], response["timeout_s"])
    assert response["validation_scope"] == "adapter_software"
    assert response["hardware_acceptance"] == "unverified"
    assert not runtime.dispatch.enabled
    runtime._device.prepare.assert_not_called()
    runtime._robot.send_action.assert_not_called()


def test_receipts_are_monotonic_one_use_and_capture_age_based():
    runtime = make_runtime(decision_max_age_s=1)
    first, images = runtime.observe()
    second, _ = runtime.observe()
    assert len(images) == 2
    assert not runtime.supervise(True)["ok"]
    assert not runtime.supervise(str(first["frame_seq"]))["ok"]
    assert runtime.supervise(second["frame_seq"])["ok"]
    assert not runtime.supervise(first["frame_seq"])["ok"]
    assert not runtime.supervise(second["frame_seq"])["ok"]
    state, _ = runtime.observe()
    with runtime._frames:
        runtime._observations[-1] = (state["frame_seq"], time.monotonic() - 2)
    assert not runtime.supervise(state["frame_seq"])["ok"]


def test_cancel_invalidates_both_acknowledged_and_pending_frames():
    runtime = make_runtime()
    first, _ = runtime.observe()
    assert runtime.supervise(first["frame_seq"])["ok"]
    pending, _ = runtime.observe()
    runtime.cancel_current("operator_takeover")
    assert not runtime.supervise(pending["frame_seq"])["ok"]
    assert not runtime._decision_positions
    fresh, _ = runtime.observe()
    assert runtime.supervise(fresh["frame_seq"])["ok"]
    runtime._robot.send_action.assert_not_called()


def test_inflight_observation_cannot_cross_a_stop():
    runtime = make_runtime()
    entered, release = threading.Event(), threading.Event()
    capture = runtime._capture
    def delayed():
        entered.set()
        assert release.wait(2)
        return capture()
    runtime._capture = delayed
    errors = []
    def work():
        try:
            runtime.observe()
        except RuntimeError as error:
            errors.append(str(error))
    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(1)
    runtime.cancel_current("operator_takeover")
    release.set()
    worker.join(2)
    assert errors and "superseded" in errors[0]
    assert not runtime._observations


def test_existing_execution_watchdog_stops_without_fresh_receipts():
    runtime = make_runtime(supervision_timeout_s=0.08, watchdog_poll_s=0.005, cancel_timeout_s=0.02)
    runtime.dispatch.enable(True)
    state, _ = runtime.observe()
    assert runtime.supervise(state["frame_seq"])["ok"]
    entered, stopped = threading.Event(), threading.Event()
    def operation(token, stop):
        entered.set()
        assert stop.wait(2)
        stopped.set()
        return {"reason": "stopped"}
    task = runtime.execution.start("fixture", operation)
    assert entered.wait(1)
    assert stopped.wait(2)
    runtime.execution.wait(task["execution_id"], 0.05)
    assert runtime.execution.status()["stop_reason"] == "supervision_lost"
    runtime._robot.send_action.assert_not_called()


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), 0, -1, True, "20"])
def test_missing_or_invalid_decision_age_cannot_advertise_capability(value):
    runtime = make_runtime(decision_max_age_s=value)
    with pytest.raises(ValueError, match="decision_max_age_s"):
        runtime.external_supervision_config()


@pytest.mark.parametrize("name", ["supervision_timeout_s", "cancel_timeout_s", "watchdog_poll_s", "max_frame_age_s", "max_action_age_s"])
def test_nonfinite_watchdog_configuration_rejected_before_device_preparation(name):
    with pytest.raises(ValueError, match="finite positive"):
        make_runtime(**{name: float("nan")})
