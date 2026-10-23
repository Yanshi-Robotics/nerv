"""Passive device discovery. Enumeration never opens a motor bus or camera."""
import json
from pathlib import Path
import sys


def camera_candidates():
    """Enumerate OS metadata without opening VideoCapture or probing devices."""
    if sys.platform.startswith("linux"):
        persistent = sorted(Path("/dev/v4l/by-id").glob("*"))
        return [{"device": str(p), "label": p.name, "stable": p in persistent,
                 "identity": str(p) if p in persistent else None}
                for p in persistent or sorted(Path("/dev").glob("video*"))]
    from cv2_enumerate_cameras import enumerate_cameras
    # Use one backend per OS and pass it explicitly to the native camera config.
    # This avoids duplicate devices and backend-dependent index mismatches.
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_AVFOUNDATION
    return [{"device": c.index, "label": c.name, "stable": bool(c.path),
             "identity": c.path or None, "backend": c.backend}
            for c in enumerate_cameras(backend)]


def resolve_cameras(cameras, identities):
    """Re-identify known cameras; never guess roles from a model name or VID/PID."""
    if not identities:
        return cameras
    candidates = camera_candidates()
    result = {name: dict(config) for name, config in cameras.items()}
    for name, identity in identities.items():
        if not identity:
            continue
        matches = [c for c in candidates if c.get("identity") == identity]
        if len(matches) != 1:
            raise ValueError(f"{name}: saved camera is missing or ambiguous; confirm its view in camera setup")
        result[name]["index_or_path"] = matches[0]["device"]
        if "backend" in matches[0]:
            result[name]["backend"] = matches[0]["backend"]
    return result


def discover(home):
    from serial.tools import list_ports
    ports = [{"port": p.device, "label": p.description, "serial": p.serial_number,
              "vid": p.vid, "pid": p.pid, "location": p.location} for p in list_ports.comports()]
    warnings = []
    try:
        cameras = camera_candidates()
    except Exception:
        cameras = []
        warnings.append("Camera enumeration failed. Check camera permissions or enter the device index manually.")
    calibration_root = Path.home() / ".cache/huggingface/lerobot/calibration/robots/so101_follower"
    calibrations = [{"id": p.stem, "directory": str(p.parent)} for p in sorted(calibration_root.glob("*.json"))]
    profile = Path(home) / "devices/standard.json"
    saved = json.loads(profile.read_text(encoding="utf-8")) if profile.exists() else None
    repair_step = None
    if saved:
        identity = saved.get("identity", {})
        matches = [p for p in ports if p.get("serial") == identity.get("serial")
                   and p.get("vid") == identity.get("vid") and p.get("pid") == identity.get("pid")] if identity.get("serial") else [p for p in ports if p["port"] == saved["port"]]
        if len(matches) == 1:
            saved["port"] = matches[0]["port"]
        else:
            repair_step = 1
        try:
            from .standard import validate_calibration
            validate_calibration(json.loads((Path(saved["calibration_dir"]) / (saved["id"] + ".json")).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            repair_step = repair_step or 2
        try:
            identities = saved.get("camera_identities", {})
            if not all(identities.get(name) for name in saved["cameras"]):
                repair_step = repair_step or 3
            else:
                saved["cameras"] = resolve_cameras(saved["cameras"], identities)
        except Exception:
            repair_step = repair_step or 3
    return {"ports": ports, "cameras": cameras, "calibrations": calibrations,
            "saved": saved, "repair_step": repair_step,
            "warnings": warnings, "camera_discovery": "os_metadata"}


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
