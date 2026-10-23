"""Standard product integration and fault tests use explicit CPU device fixtures."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from nerv_runtime_lerobot.manage import manage, inspect_checkpoint
from nerv_runtime_lerobot.standard import CHANNELS, UNITS, validate_action, validate_cameras
from test_lerobot_adapter import runtime_fixture


def checkpoint(root):
    root.mkdir()
    config = {"type": "act", "input_features": {"observation.state": {"type": "STATE", "shape": [6]},
        **{f"observation.images.{c}": {"type": "VISUAL", "shape": [3, 480, 640]} for c in ("top", "wrist")}},
        "output_features": {"action": {"type": "ACTION", "shape": [6]}}}
    (root / "config.json").write_text(json.dumps(config))
    (root / "model.safetensors").write_bytes(b"explicit-test-fixture-not-a-model")
    for name in ("pre", "post"):
        (root / f"policy_{name}processor.json").write_text(json.dumps({"steps": [{"state_file": f"{name}.safetensors"}]}))
        (root / f"{name}.safetensors").write_bytes(b"fixture")
    return root


def cameras():
    return {name: {"type": "opencv", "index_or_path": i, "width": 640, "height": 480, "fps": 30}
            for i, name in enumerate(("top", "wrist"))}


def test_configure_import_inspect_never_connects_and_preserves_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    calibration = tmp_path / "calibration"
    calibration.mkdir()
    (calibration / "student.json").write_text("{}")
    camera = tmp_path / "cameras.json"
    camera.write_text(json.dumps(cameras()))
    payload = {"port": "COM5", "id": "student", "calibration_dir": str(calibration),
               "camera_config_file": str(camera), "policy_device": "cpu"}
    assert manage("configure", payload, home)["connected"] is False
    model = checkpoint(tmp_path / "model with spaces")
    before = {p.name: p.read_bytes() for p in model.iterdir()}
    request = {"path": str(model), "id": "pick", "conditions": "fixture scene", "standard_declared": True}
    result = manage("import", request, home)
    assert result["manifest"]["semantics_source"] == "user_standard_declaration"
    assert result["manifest"]["position_units"] == UNITS
    assert len(result["manifest"]["sha256"]) == 6
    assert manage("inspect", {"id": "pick"}, home)["ok"]
    monkeypatch.setattr("nerv_runtime_sdk.policies.backend", lambda _: lambda *args: SimpleNamespace(close=lambda: None))
    assert manage("check", {"id": "pick", "device": "cpu"}, home)["loaded"]
    assert manage("policies", {}, home)["policies"][0]["check"]["state"] == "loaded"
    assert before == {p.name: p.read_bytes() for p in model.iterdir()}
    with pytest.raises(ValueError, match="already exists"):
        manage("import", request, home)
    (model / "model.safetensors").write_bytes(b"changed")
    assert manage("policies", {}, home)["policies"][0]["check"]["state"] == "changed"
    with pytest.raises(ValueError, match="fingerprint"):
        manage("check", {"id": "pick", "device": "cpu"}, home)
    assert manage("policies", {}, home)["policies"][0]["check"]["state"] == "failed"
    with pytest.raises(ValueError, match="fingerprint"):
        manage("inspect", {"id": "pick"}, home)


def test_import_rejects_missing_processors_nonstandard_inputs_and_unproven_semantics(tmp_path):
    root = checkpoint(tmp_path / "model")
    with pytest.raises(ValueError, match="confirm"):
        inspect_checkpoint(root, False)
    (root / "pre.safetensors").rename(root / "missing")
    with pytest.raises(FileNotFoundError):
        inspect_checkpoint(root, True)
    config = json.loads((root / "config.json").read_text())
    config["input_features"]["observation.state"]["shape"] = [7]
    (root / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="features"):
        inspect_checkpoint(root, True)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "2"])
def test_invalid_targets_rejected(bad):
    with pytest.raises(ValueError):
        validate_action({**dict.fromkeys(CHANNELS, 0), CHANNELS[0]: bad})


def test_camera_contract():
    validate_cameras(cameras())
    wrong = cameras()
    wrong["wrist"]["index_or_path"] = 0
    with pytest.raises(ValueError, match="different"):
        validate_cameras(wrong)


def primitive_fixture(tmp_path):
    runtime, writes = runtime_fixture()
    positions = dict.fromkeys(CHANNELS, 0.0)
    def send(action):
        writes.append(dict(action))
        positions.update(action)
        return dict(action)
    runtime._robot.send_action = send
    runtime._positions = lambda: dict(positions)
    runtime._capture = lambda: (dict(positions), {})
    runtime._device.validate_targets = validate_action
    from nerv_runtime_lerobot.runtime import LeRobotRuntime
    runtime._send_recorded = LeRobotRuntime._send_recorded.__get__(runtime)
    runtime._action_log = tmp_path / "events.jsonl"
    runtime._observations.append((1, time.monotonic()))
    runtime._decision_positions[1] = dict(positions)
    return runtime, writes


def test_primitive_uses_native_send_and_reports_requested_sent_measured(tmp_path):
    runtime, writes = primitive_fixture(tmp_path)
    result = runtime.invoke("move_joints", targets={"shoulder_pan": 1}, duration_s=0.3,
                            frame_seq=1, instance_id=runtime._instance)
    state = runtime.execution.wait(result["data"]["execution_id"], 1)
    assert state["state"] == "finished"
    assert state["result"]["target_reached"]
    assert state["result"]["task_success"] is None
    assert writes[-1]["shoulder_pan.pos"] == 1
    receipt = json.loads(runtime._action_log.read_text().splitlines()[-1])
    assert receipt["requested"] == receipt["sent"] == receipt["measured"]


def test_stale_decision_and_foreign_instance_never_write(tmp_path):
    runtime, writes = primitive_fixture(tmp_path)
    args = dict(targets={"shoulder_pan": 1}, duration_s=0.3, frame_seq=1, instance_id="old")
    with pytest.raises(ValueError, match="expired"):
        runtime.invoke("move_joints", **args)
    args["instance_id"] = runtime._instance
    runtime._observations.clear()
    runtime._observations.append((1, time.monotonic() - 100))
    with pytest.raises(ValueError, match="expired"):
        runtime.invoke("move_joints", **args)
    assert not writes


def test_clipped_native_action_is_not_reported_as_requested(tmp_path):
    runtime, writes = primitive_fixture(tmp_path)
    runtime._robot.send_action = lambda action: {**action, "shoulder_pan.pos": 0.5}
    result = runtime._write(runtime.dispatch.token(), {**dict.fromkeys(CHANNELS, 0.0), "shoulder_pan.pos": 1})
    assert result["adjusted"]
    assert result["requested"]["shoulder_pan.pos"] == 1
    assert result["sent"]["shoulder_pan.pos"] == 0.5
    assert result["measured"]["shoulder_pan.pos"] == 0


def test_lost_supervision_revokes_inference_without_replay(tmp_path):
    runtime, writes = primitive_fixture(tmp_path)
    runtime.execution.supervision_timeout_s = 0.02
    runtime.execution.poll_s = 0.005
    entered, finish = threading.Event(), threading.Event()
    def inference(token, stop):
        entered.set()
        finish.wait(1)
        runtime._write(token, dict.fromkeys(CHANNELS, 1.0))
    state = runtime.execution.start("fixture", inference)
    assert entered.wait(1)
    deadline = time.monotonic() + 1
    while runtime.execution.status().get("stop_reason") != "supervision_lost" and time.monotonic() < deadline:
        time.sleep(0.005)
    finish.set()
    runtime.execution._worker.join(1)
    state = runtime.execution.status()
    assert state["stop_reason"] == "supervision_lost"
    assert all(action == dict.fromkeys(CHANNELS, 0.0) for action in writes)


def test_mismatched_native_calibration_cannot_enable():
    runtime, writes = runtime_fixture()
    robot = SimpleNamespace(calibration={"fixture": {}}, is_calibrated=False,
        connect=lambda **kw: writes.append(kw), cameras={},
        bus=SimpleNamespace(is_connected=True, disconnect=lambda: writes.append("closed")))
    with pytest.raises(ValueError, match="differs"):
        runtime._connect_calibrated(robot)
    assert writes == [{"calibrate": False}, "closed"]


def test_camera_capture_rejects_wrong_actual_dimensions():
    import numpy as np
    from nerv_runtime_lerobot.device import DeviceSession
    device = DeviceSession({"max_frame_age_s": 0.5})
    camera = SimpleNamespace(frame_lock=threading.Lock(), latest_frame=np.zeros((4, 4, 3), dtype=np.uint8), latest_timestamp=time.perf_counter())
    device.robot = SimpleNamespace(cameras={"top": camera}, get_observation=lambda: dict.fromkeys(CHANNELS, 0.0))
    with pytest.raises(ValueError, match="captured image"):
        device.capture()


def test_observation_receipt_keeps_capture_age():
    import numpy as np
    runtime, _ = runtime_fixture()
    stamp = time.perf_counter() - 0.1
    runtime._robot.cameras = {"top": object(), "wrist": object()}
    raw = {**dict.fromkeys(CHANNELS, 0.0), **{name: np.zeros((4, 4, 3), dtype=np.uint8) for name in runtime._robot.cameras}}
    runtime._capture = lambda: (raw, {name: stamp for name in runtime._robot.cameras})
    state, images = runtime.observe()
    assert len(images) == 2
    assert state["camera_captured_at_s"]["top"] == stamp
    assert time.monotonic() - runtime._observations[-1][1] >= 0.1
