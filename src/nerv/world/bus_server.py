"""ZMQ REP server for the motor bus: one handler(dict) -> dict per message, on its own thread."""
from __future__ import annotations

import threading
import traceback
from typing import Callable

import zmq

from ..nerve import wire


class BusServer:
    def __init__(self, bind_url: str, handler: Callable[[dict], dict]) -> None:
        self.bind_url = bind_url
        self._handler = handler
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.REP)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.RCVTIMEO, 250)
        self._sock.bind(bind_url)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="nerv-bus-server", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._sock.recv()
            except zmq.Again:
                continue
            try:
                rep = self._handler(wire.loads(raw))
                if "ok" not in rep:
                    rep["ok"] = True
            except Exception as e:  # never wedge the REQ side
                rep = {"ok": False, "error": f"{type(e).__name__}: {e}",
                       "trace": traceback.format_exc(limit=3)}
            self._sock.send(wire.dumps(rep))

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close(0)
