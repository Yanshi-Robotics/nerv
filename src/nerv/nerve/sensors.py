"""Sensor streams — the unit of perception, published by whoever carries the sensor.

A stream is named ``<kind>:<name>`` (``camera:head``, ``camera:top``, ``range:front``).
A body publishes the streams mounted on it; a world publishes the ambient ones. System 1
(the body's policy) subscribes at full rate over the motor bus; System 2 (the brain) gets
one frame per step, assembled by the platform from the streams the session declares.
Ground truth is not a sensor and never travels as one.
"""
from __future__ import annotations

from dataclasses import dataclass

KIND_CAMERA = "camera"
KIND_RANGE = "range"


def parse(name: str) -> tuple[str, str]:
    kind, _, rest = (name or "").partition(":")
    if not kind or not rest:
        raise ValueError(f"sensor stream names look like 'camera:head', got {name!r}")
    return kind, rest


@dataclass
class SensorFrame:
    name: str            # "camera:head"
    mime: str            # "image/jpeg" | "image/png" | "application/json"
    data: bytes
    t: float             # publisher clock (sim seconds or unix time)
