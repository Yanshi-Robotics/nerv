"""Native adapter lifecycle tests with explicit CPU fixtures, never real devices."""
import io
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from nerv.platform.registry import Registry
from nerv_runtime_sdk.contracts import EnvironmentBinding, RobotProfile
from nerv_runtime_lerobot.runtime import LeRobotRuntime


def runtime_fixture():
    spec = Registry().body("arm-lerobot-so101")
    settings = {**spec.buses["real"].settings, "policy_device":"cpu", "cancel_timeout_s":0.01}
    # This lifecycle fixture must not depend on an installed policy or local checkpoint.
    profile = {**spec.model_dump(), "skills": {}}
    runtime = LeRobotRuntime(RobotProfile(id=spec.name, family=spec.family, configuration=profile), EnvironmentBinding(id="fixture", profile_id=spec.name, mode="real", provider="lerobot0.6.1", configuration=settings))
    runtime._artifacts = {"snack_into_box": {}}
    writes = []
    runtime._robot = SimpleNamespace(send_action=lambda action: writes.append(action))
    runtime._send_recorded = lambda action, **kwargs: runtime._robot.send_action(action)
    runtime._positions = lambda: {"joint.pos":1.0}
    runtime._capture = lambda: ({"joint.pos":1.0}, {})
    runtime.dispatch.enable(True)
    return runtime, writes


def test_cancel_during_native_inference_has_no_late_action(monkeypatch):
    monkeypatch.setattr("nerv_runtime_lerobot.policy.artifact_files", lambda _: None)  # Explicit runner fixture.
    runtime, writes = runtime_fixture()
    entered, release = threading.Event(), threading.Event()
    class Runner:
        def reset(self):
            pass
        def action(self, _):
            entered.set()
            assert release.wait(2)
            return {"joint.pos":2.0}
    runtime._runner, runtime._policy_id = Runner(), "snack_into_box"
    state = runtime.invoke("snack_into_box")["data"]
    assert entered.wait(1)
    runtime.cancel_current("fixture-cancel")
    assert writes == [{"joint.pos":1.0}]  # measured hold, never the inferred future target
    release.set()
    runtime.execution.wait(state["execution_id"], 1)
    assert writes == [{"joint.pos":1.0}]


def test_loading_and_starting_are_exclusive_without_resetting_active_runner(monkeypatch):
    monkeypatch.setattr("nerv_runtime_lerobot.policy.artifact_files", lambda _: None)  # Explicit runner fixture.
    runtime, writes = runtime_fixture()
    entered, release = threading.Event(), threading.Event()
    class Runner:
        def reset(self):
            pass
        def action(self, _):
            entered.set()
            assert release.wait(2)
            return {"joint.pos":2.0}
    runner = runtime._runner = Runner()
    runtime._policy_id = "snack_into_box"
    state = runtime.invoke("snack_into_box")["data"]
    assert entered.wait(1)
    with pytest.raises(RuntimeError, match="change policy"):
        runtime.load_policy("snack_into_box")
    with pytest.raises(RuntimeError, match="another execution"):
        runtime.invoke("snack_into_box")
    assert runtime._runner is runner
    runtime.cancel_current("fixture")
    release.set()
    runtime.execution.wait(state["execution_id"], 1)


def test_two_named_streams_share_native_camera_capture_and_reject_stale_frames():
    runtime, _ = runtime_fixture()
    now = time.perf_counter()
    cameras = {name: SimpleNamespace(frame_lock=threading.Lock(), latest_frame=np.zeros((4, 4, 3), dtype=np.uint8), latest_timestamp=now) for name in ("top", "wrist")}
    runtime._robot.cameras = cameras
    assert set(runtime.sensor_names()) == {"camera:top", "camera:wrist"}
    from PIL import Image
    assert Image.open(io.BytesIO(runtime.sensor_jpeg("camera:top"))).size == (4, 4)
    cameras["wrist"].latest_timestamp = now - 10
    with pytest.raises(TimeoutError, match="stale"):
        runtime.sensor_jpeg("camera:wrist")


def test_artifact_fingerprint_mismatch_refuses_loading(tmp_path, monkeypatch):
    from nerv_runtime_lerobot.policy import artifact_files
    monkeypatch.setenv("FIXTURE_CHECKPOINT", str(tmp_path))
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="fingerprint"):
        artifact_files({"root_env":"FIXTURE_CHECKPOINT", "sha256":{"config.json":"wrong"}})


def test_unregistered_calibration_is_refused_before_connecting():
    runtime, writes = runtime_fixture()
    robot = SimpleNamespace(calibration={}, connect=lambda **_: writes.append("connected"))
    with pytest.raises(ValueError, match="calibration"):
        runtime._connect_calibrated(robot)
    assert writes == []


def test_partial_native_connection_failure_releases_acquired_resources():
    runtime, writes = runtime_fixture()
    def component(name):
        return SimpleNamespace(is_connected=True, disconnect=lambda: writes.append(name))
    robot = SimpleNamespace(calibration={"fixture_joint":{}}, cameras={"one":component("camera-one"), "two":component("camera-two")}, bus=component("bus"))
    def connect(**_):
        raise RuntimeError("fixture partial connection failure")
    robot.connect = connect
    with pytest.raises(RuntimeError, match="partial connection"):
        runtime._connect_calibrated(robot)
    assert writes == ["camera-one", "camera-two", "bus"]
