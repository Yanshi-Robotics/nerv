"""One bus address: prompt motor replies and a bounded, independent camera worker.

The ROUTER thread alone owns its socket. REQ clients keep the existing JSON protocol;
only sensor handlers run concurrently, and world access retains the physics lock.
"""
from __future__ import annotations

import threading
import traceback
import queue
from typing import Callable

import zmq

from ..nerve import wire
from ..nerve.world import OP_SENSOR

# Poll only bounds camera-result delivery/shutdown; arriving motor requests wake immediately.
POLL_MS = 10
START_TIMEOUT_S = 2.0


class BusServer:
    def __init__(self, bind_url: str, handler: Callable[[dict], dict]) -> None:
        self.bind_url = bind_url
        self._handler = handler
        self._ctx = zmq.Context.instance()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._start_error = None
        self._camera_jobs = queue.Queue(maxsize=1)
        self._camera_done = queue.Queue(maxsize=1)
        self._camera_busy = False
        self._camera_thread = threading.Thread(target=self._camera_loop, name="nerv-bus-camera", daemon=True)
        self._thread = threading.Thread(target=self._loop, name="nerv-bus-server", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT_S):
            self._stop.set()
            raise TimeoutError("motor bus did not bind")
        if self._start_error is not None:
            raise self._start_error
        self._camera_thread.start()

    def _reply(self, msg: dict) -> bytes:
        try:
            rep = self._handler(msg)
            if "ok" not in rep:
                rep["ok"] = True
        except Exception as error:
            rep = {"ok": False, "error": f"{type(error).__name__}: {error}",
                   "trace": traceback.format_exc(limit=3)}
        return wire.dumps(rep)

    def _camera_loop(self) -> None:
        while not self._stop.is_set():
            try:
                envelope, msg = self._camera_jobs.get(timeout=POLL_MS / 1000)
            except queue.Empty:
                continue
            self._camera_done.put((envelope, self._reply(msg)))

    def _loop(self) -> None:
        sock = self._ctx.socket(zmq.ROUTER)
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.bind(self.bind_url)
        except Exception as error:
            self._start_error = error
            self._ready.set()
            sock.close(0)
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                try:
                    envelope, reply = self._camera_done.get_nowait()
                    sock.send_multipart([*envelope, reply])
                    self._camera_busy = False
                except queue.Empty:
                    pass
                if not sock.poll(POLL_MS):
                    continue
                frames = sock.recv_multipart()
                envelope, raw = frames[:-1], frames[-1]
                try:
                    msg = wire.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError("bus message must be an object")
                except Exception as error:
                    sock.send_multipart([*envelope, wire.dumps({"ok": False, "error": str(error)})])
                    continue
                if msg.get("op") == OP_SENSOR:
                    if self._camera_busy:
                        sock.send_multipart([*envelope, wire.dumps({"ok": False, "error": "camera busy"})])
                    else:
                        self._camera_busy = True
                        self._camera_jobs.put_nowait((envelope, msg))
                else:
                    sock.send_multipart([*envelope, self._reply(msg)])
        finally:
            sock.close(0)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        # A render already in progress is bounded by RenderService's timeout; do not
        # delay motor-server shutdown while waiting for graphics to recover.
