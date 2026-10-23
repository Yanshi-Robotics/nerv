"""User-facing identity independent of calibration and hardware configuration."""
import io
import json
import time
import warnings
from pathlib import Path

from filelock import FileLock
from PIL import Image, ImageOps

from .. import paths
from ..installation import write_json

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Keep local portraits small enough for backups.
MAX_IMAGE_PIXELS = 20_000_000  # Bound decoding memory for user supplied images.
PORTRAIT_SIZE = 768  # Square card image; the original is never required afterwards.


def root():
    return Path(paths.NERV_HOME) / "devices"


def read():
    directory = root()
    path = directory / "display.json"
    display = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    standard = directory / "standard.json"
    device = json.loads(standard.read_text(encoding="utf-8")) if standard.exists() else {}
    return {"name": display.get("name", "SO-101"), "registered": bool(device),
            "image": "/api/robot/image?v=" + str(display.get("image_version", 0)) if (directory / "portrait.webp").exists() else None,
            "device": {key: device.get(key) for key in ("id", "port", "calibration_dir", "cameras", "policy_device")}}


def save_name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("Use a robot name between 1 and 80 characters")
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(directory / "display.lock"):
        path = directory / "display.json"
        value = json.loads(path.read_text()) if path.exists() else {}
        write_json(path, {**value, "name": name.strip()})
    return read()


def save_image(content):
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be at most 5 MB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in ("JPEG", "PNG", "WEBP") or source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Choose a JPEG, PNG or WebP image up to 20 megapixels")
            portrait = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (PORTRAIT_SIZE, PORTRAIT_SIZE))
            result = io.BytesIO()
            portrait.save(result, format="WEBP", quality=85)
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(directory / "display.lock"):
        target = directory / "portrait.webp"
        temporary = directory / "portrait.tmp"
        temporary.write_bytes(result.getvalue())
        temporary.replace(target)
        path = directory / "display.json"
        value = json.loads(path.read_text()) if path.exists() else {}
        write_json(path, {**value, "image_version": time.time_ns()})
    return read()


def reset_image():
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(directory / "display.lock"):
        (directory / "portrait.webp").unlink(missing_ok=True)
    return read()
