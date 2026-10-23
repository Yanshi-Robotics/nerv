"""Passive device discovery. Enumeration never opens a motor bus or camera."""
import json
from pathlib import Path
import sys


def discover(home):
    from serial.tools import list_ports
    ports = [{"port": p.device, "label": p.description, "serial": p.serial_number,
              "vid": p.vid, "pid": p.pid, "location": p.location} for p in list_ports.comports()]
    cameras = []
    if sys.platform.startswith("linux"):
        persistent = sorted(Path("/dev/v4l/by-id").glob("*"))
        for path in persistent or sorted(Path("/dev").glob("video*")):
            cameras.append({"device": str(path), "label": path.name, "stable": path in persistent})
    calibration_root = Path.home() / ".cache/huggingface/lerobot/calibration/robots/so101_follower"
    calibrations = [{"id": p.stem, "directory": str(p.parent)} for p in sorted(calibration_root.glob("*.json"))]
    profile = Path(home) / "devices/standard.json"
    return {"ports": ports, "cameras": cameras, "calibrations": calibrations,
            "saved": json.loads(profile.read_text(encoding="utf-8")) if profile.exists() else None,
            "camera_discovery": "persistent_paths" if sys.platform.startswith("linux") else "manual_index_required"}


def resolve_port(settings):
    identity = settings.get("device_identity", {})
    if not identity.get("serial"):
        return settings["port"]
    from serial.tools import list_ports
    matches = [p.device for p in list_ports.comports() if p.serial_number == identity["serial"]
               and p.vid == identity.get("vid") and p.pid == identity.get("pid")]
    if len(matches) != 1:
        raise ValueError("Saved arm identity is missing or ambiguous; repair the device selection")
    return matches[0]
