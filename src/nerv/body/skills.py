"""The skill runner: how a body node runs a learned behaviour for as long as one command lasts.

A skill is a function `fn(progress, should_abort) -> dict` that loops at policy rate, reports
progress (a sign of life for the platform's liveness watchdog) and checks `should_abort()`
every tick. It ends on done / stalled / fallen / timeout / stop and returns a measured result.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class StopFlag:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def request(self) -> None:
        self._ev.set()

    def clear(self) -> None:
        self._ev.clear()

    def is_set(self) -> bool:
        return self._ev.is_set()


class SkillRunner:
    def __init__(self, stop: StopFlag) -> None:
        self.stop = stop
        self._active = threading.Lock()
        self.running: str = ""

    def run(self, name: str, fn: Callable, _progress=None) -> dict:
        if not self._active.acquire(blocking=False):
            return {"ok": False, "message": f"another skill ({self.running}) is still running"}
        self.stop.clear()
        self.running = name
        t0 = time.monotonic()

        def progress(p: float, msg: str = "") -> None:
            if _progress is not None:
                try:
                    _progress(float(p), msg)
                except Exception:
                    pass

        try:
            res = fn(progress, self.stop.is_set)
            if not isinstance(res, dict):
                res = {"ok": True, "message": str(res)}
            res.setdefault("data", {})
            res["data"]["elapsed_s"] = round(time.monotonic() - t0, 2)
            if self.stop.is_set():
                res["data"]["stopped"] = True
            return res
        finally:
            self.running = ""
            self._active.release()
