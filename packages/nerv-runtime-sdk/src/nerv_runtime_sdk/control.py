"""One writer, revocable execution generations and nonblocking supervision."""
from __future__ import annotations

import threading
import time
import uuid


class StopFlag:
    def __init__(self):
        self._ev = threading.Event()

    def request(self):
        self._ev.set()

    def clear(self):
        self._ev.clear()

    def is_set(self):
        return self._ev.is_set()


class DispatchAuthority:
    def __init__(self):
        self._lock = threading.RLock()
        self._generation = 0
        self.enabled = False
        self.held = False
        self._receipts = {}

    def enable(self, enabled: bool):
        with self._lock:
            if self.held and enabled:
                raise RuntimeError("emergency stop is latched")
            self._generation += 1
            self.enabled = enabled

    def token(self) -> int:
        with self._lock:
            if not self.enabled or self.held:
                raise RuntimeError("runtime is not enabled")
            self._receipts.setdefault(self._generation, {"accepted": 0, "rejected": 0})
            return self._generation

    def receipt(self, token):
        with self._lock:
            return dict(self._receipts.get(token, {}))

    def write(self, token: int, operation):
        with self._lock:
            if token != self._generation or not self.enabled or self.held:
                self._receipts.setdefault(token, {"accepted": 0, "rejected": 0})["rejected"] += 1
                raise InterruptedError("execution no longer owns actuator dispatch")
            result = operation()
            receipt = self._receipts.setdefault(token, {"accepted": 0, "rejected": 0})
            receipt["accepted"] += 1
            receipt["last_dispatch_monotonic"] = time.monotonic()
            return result

    def revoke(self, *, hold: bool = False):
        with self._lock:
            self._receipts.setdefault(self._generation, {"accepted": 0, "rejected": 0})["revoked_monotonic"] = time.monotonic()
            self._generation += 1
            if hold:
                self.held = True
                self.enabled = False

    def release(self):
        with self._lock:
            self._generation += 1
            self.held = False
            self.enabled = False


class ManagedExecutions:
    def __init__(self, dispatch: DispatchAuthority, *, supervision_timeout_s: float,
                 cancel_timeout_s: float, poll_s: float):
        if min(supervision_timeout_s, cancel_timeout_s, poll_s) <= 0:
            raise ValueError("execution deadlines must be positive")
        self.dispatch = dispatch
        self.supervision_timeout_s = supervision_timeout_s
        self.cancel_timeout_s = cancel_timeout_s
        self.poll_s = poll_s
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = None
        self._last_supervision = time.monotonic()
        self._state: dict = {"state": "idle", "running": False}
        self.on_stop = lambda: None
        self._finalizer_lock = self._lock
        self._stop_applied = True

    def _apply_stop(self):
        with self._finalizer_lock:
            if not self._stop_applied:
                self.on_stop()
                self._stop_applied = True

    def supervise(self):
        with self._lock:
            self._last_supervision = time.monotonic()

    def status(self):
        with self._lock:
            return {**self._state, "running": bool(self._worker and self._worker.is_alive()),
                    "dispatch": self.dispatch.receipt(self._state.get("dispatch_generation"))}

    def start(self, name: str, operation, expected_token=None):
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("another execution is still running")
            token = self.dispatch.token()
            if expected_token is not None and token != expected_token:
                raise InterruptedError("execution was cancelled during preparation")
            self._stop_applied = False
            self._stop = threading.Event()
            stop = self._stop
            run_id = uuid.uuid4().hex
            self._state = {"execution_id": run_id, "name": name, "state": "running",
                           "started_at": time.time(), "running": True, "dispatch_generation": token}
            self._last_supervision = time.monotonic()

            def run():
                result, state = {}, "stopped"
                try:
                    result = operation(token, stop)
                    state = "stopped" if stop.is_set() else "finished"
                except InterruptedError:
                    state = "stopped"
                except Exception as error:
                    result = {"error": f"{type(error).__name__}: {error}"}
                    state = "failed"
                finally:
                    with self._lock:
                        self.dispatch.revoke()
                        stop.set()
                        try:
                            self._apply_stop()
                        except Exception as error:
                            result = {"error": f"stop acknowledgement failed: {type(error).__name__}"}
                            state = "failed"
                        self._state.update(state=state, result=result, ended_at=time.time(), running=False)

            self._worker = threading.Thread(target=run, name=f"runtime-{run_id}", daemon=True)
            worker = self._worker
            worker.start()

            def watchdog():
                while worker.is_alive() and not stop.wait(self.poll_s):
                    if time.monotonic() - self._last_supervision > self.supervision_timeout_s:
                        self.cancel(run_id, "supervision_lost")
                        return

            threading.Thread(target=watchdog, name=f"watchdog-{run_id}", daemon=True).start()
            return self.status()

    def cancel(self, execution_id: str = "", reason: str = "cancelled"):
        with self._lock:
            if execution_id and execution_id != self._state.get("execution_id"):
                raise ValueError("execution id does not match current runtime")
            self._stop.set()
            self._state["stop_reason"] = reason
            worker = self._worker
            run_id = self._state.get("execution_id")
            # Finalization and new starts share this lock. A delayed old cancel
            # cannot revoke or hold a subsequent execution.
            self.dispatch.revoke()
            self._apply_stop()
            receipt = dict(self._state)
        if worker and worker is not threading.current_thread():
            worker.join(self.cancel_timeout_s)
        return {**(self.status() if self.status().get("execution_id") == run_id else receipt), "dispatch_stopped": True}

    def wait(self, execution_id: str, seconds: float):
        if seconds < 0 or seconds > self.supervision_timeout_s:
            raise ValueError("wait exceeds supervision deadline")
        if execution_id != self._state.get("execution_id"):
            raise ValueError("unknown execution")
        if self._worker:
            self._worker.join(seconds)
        return self.status()
