"""Versioned course product contract; never a user-defined joint mapping."""
import math

PRODUCT = "so101-standard-v1"
LEROBOT_VERSION = "0.6.1"
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
CHANNELS = tuple(f"{name}.pos" for name in (*JOINTS, "gripper"))
CAMERAS = ("top", "wrist")
WIDTH, HEIGHT, FPS = 640, 480, 30
UNITS = {"arm_joints": "degrees", "gripper": "percent (RANGE_0_100)"}


def validate_cameras(cameras):
    if set(cameras) != set(CAMERAS):
        raise ValueError("standard cameras must be named top and wrist")
    for name, camera in cameras.items():
        if camera.get("type", "opencv") != "opencv":
            raise ValueError(f"{name}: standard cameras require OpenCV")
        if (camera.get("width"), camera.get("height"), camera.get("fps")) != (WIDTH, HEIGHT, FPS):
            raise ValueError(f"{name}: expected {WIDTH} x {HEIGHT} at {FPS} FPS")
        if camera.get("index_or_path") is None:
            raise ValueError(f"{name}: camera device is missing")
    if cameras["top"]["index_or_path"] == cameras["wrist"]["index_or_path"]:
        raise ValueError("top and wrist must use different camera devices")


def validate_action(action, *, complete=True):
    if not isinstance(action, dict) or not action:
        raise ValueError("action must contain named position targets")
    if (complete and set(action) != set(CHANNELS)) or not set(action) <= set(CHANNELS):
        raise ValueError("action does not match the standard SO-101 channels")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in action.values()):
        raise ValueError("action values must be finite numbers")
    if "gripper.pos" in action and not 0 <= action["gripper.pos"] <= 100:
        raise ValueError("gripper target must be between 0 and 100")
    return dict(action)


def validate_manifest(manifest):
    if manifest.get("state_names") != list(CHANNELS) or manifest.get("action_names") != list(CHANNELS):
        raise ValueError("policy channel order does not match standard SO-101")
    if manifest.get("position_units") != UNITS:
        raise ValueError("policy must use degrees and the native 0-100 gripper convention")
    if manifest.get("image_names") != list(CAMERAS):
        raise ValueError("policy cameras must be top and wrist")
    inputs = manifest.get("input_features", {})
    expected = {"observation.state": [len(CHANNELS)], **{
        f"observation.images.{name}": [3, HEIGHT, WIDTH] for name in CAMERAS}}
    if set(inputs) != set(expected) or any(inputs[k].get("shape") != shape for k, shape in expected.items()):
        raise ValueError("policy input features do not match standard SO-101")
