"""A session-scoped, durable event journal; views and summaries never erase history."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from filelock import FileLock
from contextlib import contextmanager
from pathlib import Path

_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()
_transactions = threading.local()


class SessionMemory:
    def __init__(self, root: str | Path, session_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("invalid session id")
        self.directory = Path(root) / session_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "events.jsonl"
        with _guard:
            self._lock = _locks.setdefault(str(self.path.resolve()), threading.RLock())

    @contextmanager
    def transaction(self):
        # Reentrant across memory views in one thread; flock also excludes other hosts.
        with self._lock:
            active = getattr(_transactions, "paths", set())
            key = str(self.path.resolve())
            if key in active:
                yield
                return
            with FileLock(self.directory / ".lock"):
                # OS-backed lock works on Windows and POSIX.
                _transactions.paths = active | {key}
                try:
                    yield
                finally:
                    _transactions.paths = active

    def events(self) -> list[dict]:
        with self.transaction():
            return self._read_events()

    def _read_events(self) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_bytes().splitlines(keepends=True)
        out = []
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                if index == len(lines) - 1 and not line.endswith(b"\n"):
                    raise ValueError("session journal has an incomplete final event; recover before writing")
                raise ValueError(f"corrupt session event {index + 1}") from None
            if event.get("seq") != len(out) + 1:
                raise ValueError("non-contiguous session event sequence")
            out.append(event)
        return out

    def _append(self, events: list[dict], kind: str, data: dict) -> dict:
        event = {"seq": len(events) + 1, "time": time.time(), "kind": kind, "data": data}
        encoded = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode()
        with open(self.path, "ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def append(self, kind: str, data: dict) -> dict:
        with self.transaction():
            return self._append(self.events(), kind, data)

    def read_range(self, start: int = 1, limit: int = 20) -> list[dict]:
        if start < 1 or not 1 <= limit <= 100:
            raise ValueError("history requires start >= 1 and limit between 1 and 100")
        return [e for e in self.events() if e["seq"] >= start][:limit]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("give a nonempty history query and a bounded limit")
        needle = query.casefold()
        # Provider continuation is private protocol state, not searchable prose.
        found = []
        for event in self.events():
            if event["kind"] != "message":
                continue
            data = {k: v for k, v in event["data"].items() if k != "continuation"}
            if needle in json.dumps(data, ensure_ascii=False).casefold():
                found.append({**event, "data": data})
        return found[-limit:]

    def messages(self) -> list[dict]:
        return [{**e["data"], "seq": e["seq"]} for e in self.events() if e["kind"] == "message"]

    def summary(self) -> dict | None:
        return next((e["data"] for e in reversed(self.events()) if e["kind"] == "compaction"), None)

    @staticmethod
    def fingerprint(events: list[dict]) -> str:
        return hashlib.sha256(json.dumps(events, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def commit_summary(self, start: int, end: int, text: str, fingerprint: str,
                       model: str, usage: dict | None, cancelled=lambda: False) -> bool:
        with self.transaction():
            events = self.events()
            source = [e for e in events if start <= e["seq"] <= end]
            if cancelled() or not text.strip() or self.fingerprint(source) != fingerprint:
                return False
            original = json.dumps(source, ensure_ascii=False)
            if len(text.encode()) >= len(original.encode()):
                return False
            self._append(events, "compaction", {"start": start, "end": end, "text": text,
                                               "model": model, "usage": usage})
            return True

    def flush(self) -> None:
        # Every append is durable already. Exposed for the host's dispatch barrier.
        if self.path.exists():
            with open(self.path, "rb") as stream:
                os.fsync(stream.fileno())

    def recover_runs(self) -> None:
        events = self.events()
        started = {e["data"]["run_id"] for e in events
                   if e["kind"] == "run_started" and "run_id" in e["data"]}
        ended = {e["data"]["run_id"] for e in events
                 if e["kind"] in ("run_ended", "run_interrupted") and "run_id" in e["data"]}
        for run_id in started - ended:
            self.append("run_interrupted", {"run_id": run_id, "outcome": "unknown",
                                             "reason": "host restarted; actions are not replayed"})
