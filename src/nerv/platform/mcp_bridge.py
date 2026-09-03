"""Sync ↔ async bridge for the MCP SDK, plus liveness supervision for long actions.

A robot action may take tens of seconds. We do not time out on duration; we time out on
silence: progress notifications are signs of life, a fixed hard cap is the safety belt,
and the caller may abort at any watchdog tick.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Awaitable, Callable, Optional, TypeVar

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .. import config

_T = TypeVar("_T")
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


class NodeCallError(Exception):
    pass


class LivenessTimeout(NodeCallError):
    pass


class HardCapTimeout(NodeCallError):
    pass


class CallAborted(NodeCallError):
    pass


class Beat:
    def __init__(self) -> None:
        self._last = time.monotonic()

    def touch(self) -> None:
        self._last = time.monotonic()

    def age(self) -> float:
        return time.monotonic() - self._last


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="nerv-mcp-loop", daemon=True).start()
            _loop = loop
        return _loop


def run_sync(coro: Awaitable[_T], timeout: float) -> _T:
    fut = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    return fut.result(timeout=timeout)


def run_alive(coro: Awaitable[_T], *, beat: Beat, liveness_s: float, hard_cap_s: float,
              should_abort: Optional[Callable[[], bool]] = None) -> _T:
    async def _supervise() -> _T:
        task = asyncio.ensure_future(coro)
        start = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=config.BRIDGE_WATCHDOG_POLL_S)
                if done:
                    return task.result()
                if should_abort is not None and should_abort():
                    raise CallAborted("the caller gave up waiting")
                if beat.age() > liveness_s:
                    raise LivenessTimeout(f"no sign of life for {liveness_s:g}s")
                if time.monotonic() - start > hard_cap_s:
                    raise HardCapTimeout(f"exceeded the overall cap of {hard_cap_s:g}s")
        finally:
            if not task.done():
                task.cancel()

    fut = asyncio.run_coroutine_threadsafe(_supervise(), _ensure_loop())
    return fut.result(timeout=hard_cap_s + config.BRIDGE_GRACE_S)


async def with_session(mcp_url: str, op: Callable[[ClientSession], Awaitable[_T]], timeout: float,
                       read_timeout: float | None = None) -> _T:
    t = httpx.Timeout(timeout, read=read_timeout if read_timeout is not None else timeout)
    async with httpx.AsyncClient(timeout=t, follow_redirects=True) as hc:
        async with streamable_http_client(mcp_url, http_client=hc) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                return await op(session)
