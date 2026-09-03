"""Per-session stop flag. Process memory on purpose: it describes a running turn, not memory."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_requested: set[str] = set()


def request(session_id: str) -> None:
    with _lock:
        _requested.add(session_id)


def is_set(session_id: str) -> bool:
    with _lock:
        return session_id in _requested


def clear(session_id: str) -> None:
    with _lock:
        _requested.discard(session_id)
