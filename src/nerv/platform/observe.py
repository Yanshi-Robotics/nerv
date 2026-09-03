"""Assemble the brain's observation: the body's own senses plus the ambient streams the
session declared, fetched straight from the world node. Sensors only — a stream must be one
the world advertises, and a JSON payload that looks like ground truth is refused.
"""
from __future__ import annotations

import io
import json

from ..nerve.body import Observation
from .client import RemoteBody, WorldControl

# keys that mean "where the robot is" in a simulator's own frame — never a sensor reading
TRUTH_KEYS = {"pos", "position", "xyz", "pose", "room", "yaw_world", "world_xy", "truth"}


class TruthLeak(ValueError):
    pass


def _png(data: bytes, mime: str) -> bytes:
    if mime.startswith("image/png"):
        return data
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def assemble(body: RemoteBody | None, world: WorldControl | None, ambient: list[str]) -> Observation | None:
    if body is None:
        return None
    obs = body.perceive()
    if world is not None and ambient:
        offered = set(world.sensors())
        for name in ambient:
            if name not in offered:
                raise TruthLeak(f"`{name}` is not a sensor stream this world publishes ({sorted(offered)})")
            got = world.sensor(name)
            if got is None:
                obs.state.setdefault("sensor_errors", {})[name] = "unavailable"
                continue
            data, mime = got
            if mime.startswith("application/json"):
                payload = json.loads(data.decode("utf-8") or "{}")
                if isinstance(payload, dict) and (set(payload) & TRUTH_KEYS):
                    raise TruthLeak(f"stream `{name}` carries ground-truth keys {sorted(set(payload) & TRUTH_KEYS)}")
                obs.state.setdefault("ambient", {})[name] = payload
                continue
            obs.images.append({"name": name, "png": _png(data, mime)})
        obs.state["cameras"] = [i["name"] for i in obs.images]
    return obs
