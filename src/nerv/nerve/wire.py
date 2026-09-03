"""Wire format for the NERV/World motor bus over ZMQ: one JSON object per message.

Request:  {"op": "<op>", ...fields}
Reply:    {"ok": true, ...fields}  or  {"ok": false, "error": "<message>"}
Binary payloads (sensor frames) travel base64 in the "data" field with a "mime".
"""
from __future__ import annotations

import base64
import json
from typing import Any


def dumps(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_default).encode("utf-8")


def loads(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _default(o: Any):
    try:
        import numpy as np  # noqa: WPS433
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
    except Exception:
        pass
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")
