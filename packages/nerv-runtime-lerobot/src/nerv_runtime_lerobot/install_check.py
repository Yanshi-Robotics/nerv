"""Validate native imports and product configuration without creating devices."""
import json
from importlib.metadata import version


def main():
    from .standard import LEROBOT_VERSION, WIDTH, HEIGHT, FPS
    if version("lerobot") != LEROBOT_VERSION:
        raise ValueError(f"This adapter requires LeRobot {LEROBOT_VERSION}")
    import torch
    import cv2
    import scservo_sdk
    from cv2_enumerate_cameras import enumerate_cameras
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.robots.so_follower.config_so_follower import SOFollowerRobotConfig
    from lerobot.cameras.opencv import OpenCVCameraConfig
    # Importing these classes must never connect cameras or a servo bus.
    assert all((scservo_sdk, enumerate_cameras, ACTPolicy, make_pre_post_processors, SOFollowerRobotConfig))
    config = OpenCVCameraConfig(index_or_path=0, width=WIDTH, height=HEIGHT, fps=FPS)
    assert config.width == WIDTH and torch.tensor([1.0], device="cpu").isfinite().all()
    print(json.dumps({"ok": True, "lerobot": LEROBOT_VERSION, "torch": torch.__version__,
                      "opencv": cv2.__version__, "hardware_connected": False}))


if __name__ == "__main__":
    main()
