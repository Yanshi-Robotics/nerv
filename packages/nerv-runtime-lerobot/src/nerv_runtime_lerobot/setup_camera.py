"""Isolated setup preview; owns cameras until explicitly closed, never creates a robot."""
import base64
import json
import sys
import time


PREVIEW_MAX_FRAME_AGE_S = 1.0  # Setup preview freshness; no motion is permitted.

def main():
    from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig
    import cv2
    cameras = {}
    def close():
        errors = []
        for camera in cameras.values():
            try:
                if camera.is_connected:
                    camera.disconnect()
            except Exception as error:
                errors.append(str(error))
        cameras.clear()
        if errors:
            raise RuntimeError("Camera cleanup failed: " + "; ".join(errors))
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request["operation"] == "open":
                    close()
                    from .standard import validate_cameras
                    validate_cameras(request["cameras"])
                    for name, cfg in request["cameras"].items():
                        camera = OpenCVCamera(OpenCVCameraConfig(**{k: v for k, v in cfg.items() if k != "type"}))
                        cameras[name] = camera
                        camera.connect()
                    result = {"ok": True}
                elif request["operation"] == "close":
                    close()
                    result = {"ok": True}
                elif request["operation"] == "frames":
                    result = {"ok": True, "frames": {}}
                    for name, camera in cameras.items():
                        with camera.frame_lock:
                            frame, stamp = camera.latest_frame, camera.latest_timestamp
                        if frame is None or stamp is None or time.perf_counter() - stamp > PREVIEW_MAX_FRAME_AGE_S:
                            raise ValueError(f"{name}: no fresh camera frame")
                        ok, blob = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                        if not ok:
                            raise ValueError("Preview encoding failed")
                        result["frames"][name] = "data:image/jpeg;base64," + base64.b64encode(blob).decode()
                else:
                    raise ValueError("Unknown camera operation")
            except Exception as error:
                result = {"ok": False, "message": str(error)}
            print(json.dumps(result), flush=True)
    finally:
        close()


if __name__ == "__main__":
    main()
