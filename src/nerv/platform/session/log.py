"""One log. Every signal that crosses NERV lands here, one JSONL file per session.

Envelope: {"id", "t", "ts", "session", "kind", ...payload}
kinds: llm_call · body_call · tool_call · world_call · gate · operator
The session tag rides a contextvar so LLM, body and tool calls from one turn share it.
A short in-memory ring of the same entries feeds the operator UI's live signal view.
"""
from __future__ import annotations

import collections
import contextvars
import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from ... import config, paths

_DIR = os.path.join(paths.LOGS_DIR, "sessions")
_SEQ = 0
_session_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("nerv_session", default="")
_ring: collections.deque = collections.deque(maxlen=config.SIGNAL_LOG_MAXLEN)
_lock = threading.Lock()


@contextmanager
def session_scope(session_id: str):
    tok = _session_ctx.set(session_id or "")
    try:
        yield
    finally:
        _session_ctx.reset(tok)


def bind_session(session_id: str) -> None:
    _session_ctx.set(session_id or "")


def bound_stream(session_id: str, gen):
    """Wrap a sync generator so every step runs with the session tag bound (SSE endpoints)."""
    ctx = contextvars.copy_context()
    ctx.run(bind_session, session_id)
    while True:
        try:
            yield ctx.run(next, gen)
        except StopIteration:
            return


def current_session() -> str:
    return _session_ctx.get()


def _file_for(session: str) -> str:
    if session:
        return os.path.join(_DIR, f"session-{session}.jsonl")
    return os.path.join(_DIR, "misc-" + time.strftime("%Y-%m-%d") + ".jsonl")


def record(kind: str, payload: dict) -> dict:
    global _SEQ
    with _lock:
        _SEQ += 1
        seq = _SEQ
    entry = {"id": seq, "t": round(time.time(), 3), "ts": time.strftime("%H:%M:%S"),
             "session": _session_ctx.get(), "kind": kind}
    entry.update(payload)
    _ring.append(entry)
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_file_for(entry["session"]), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return entry


def record_llm(model: str, system: str, history: list, tools: list, has_image: bool,
               reply: Any, ms: float, error: str = "") -> None:
    last_user = next((m.get("text", "") for m in reversed(history or []) if m.get("role") == "user"), "")
    record("llm_call", {
        "model": model, "system": (system or "")[:config.LOG_MAX_SYSTEM],
        "last_user": (last_user or "")[:config.LOG_MAX_USER],
        "n_history": len(history or []), "n_tools": len(tools or []), "has_image": bool(has_image),
        "reply": (getattr(reply, "text", "") or "")[:config.LOG_MAX_REPLY],
        "tool_calls": [tc.name for tc in (getattr(reply, "tool_calls", None) or [])],
        "tokens": getattr(reply, "usage", None), "ms": round(ms, 1), "error": error})


def record_node(kind: str, node: str, method: str, summary: str, ms: float, resp: dict | None = None) -> None:
    record(kind, {"node": node, "method": method, "summary": summary, "ms": round(ms, 1),
                  "resp": resp or {}})


def recent_ring(since_id: int = 0) -> list[dict]:
    return [e for e in list(_ring) if e["id"] > since_id]


def _read_jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def recent(limit: int = 300, session: str = "", kinds: Iterable[str] | None = None) -> list[dict]:
    try:
        if not os.path.isdir(_DIR):
            return []
        if session:
            entries = _read_jsonl(_file_for(session))
        else:
            entries = []
            for fn in os.listdir(_DIR):
                if fn.endswith(".jsonl"):
                    entries += _read_jsonl(os.path.join(_DIR, fn))
        if kinds is not None:
            allowed = set(kinds)
            entries = [e for e in entries if e.get("kind") in allowed]
        entries.sort(key=lambda e: (e.get("t", 0.0), e.get("id", 0)))
        return entries[-limit:]
    except Exception:
        return []


def sessions() -> list[str]:
    try:
        if not os.path.isdir(_DIR):
            return []
        files = [f for f in os.listdir(_DIR) if f.startswith("session-") and f.endswith(".jsonl")]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(_DIR, f)), reverse=True)
        return [f[len("session-"):-len(".jsonl")] for f in files]
    except Exception:
        return []


class LoggingLLM:
    """Wrap a provider so every model call is recorded (kind=llm_call)."""

    def __init__(self, inner, name: str) -> None:
        self._inner = inner
        self._name = name
        self.model = getattr(inner, "model", name)
        self.vision = getattr(inner, "vision", False)

    def chat(self, system: str, history: list, tools: list, images):
        t0 = time.perf_counter()
        reply = None
        error = ""
        try:
            reply = self._inner.chat(system, history, tools, images)
            return reply
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            raise
        finally:
            try:
                record_llm(self.model, system, history, tools, bool(images), reply,
                           (time.perf_counter() - t0) * 1000, error)
            except Exception:
                pass
