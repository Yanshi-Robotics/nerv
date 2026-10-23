"""Role/task views over the original session journal, not separate transcripts."""
from __future__ import annotations

from .memory import SessionMemory


class ScopedMemory:
    def __init__(self, journal: SessionMemory, scope: str):
        self.journal, self.scope = journal, scope

    def events(self):
        return [{"seq": e["seq"], "time": e["time"], "kind": e["data"]["kind"],
                 "data": e["data"]["data"]} for e in self.journal.events()
                if e["kind"] == "scoped_event" and e["data"]["scope"] == self.scope]

    def append(self, kind, data):
        e = self.journal.append("scoped_event", {"scope": self.scope, "kind": kind, "data": data})
        return {"seq": e["seq"], "time": e["time"], "kind": kind, "data": data}

    def messages(self):
        return [{**e["data"], "seq": e["seq"]} for e in self.events() if e["kind"] == "message"]

    def summary(self):
        return next((e["data"] for e in reversed(self.events()) if e["kind"] == "compaction"), None)

    fingerprint = staticmethod(SessionMemory.fingerprint)

    def commit_summary(self, start, end, text, fingerprint, model, usage, cancelled=lambda: False):
        import json
        with self.journal.transaction():
            source = [e for e in self.events() if start <= e["seq"] <= end]
            if (cancelled() or not text.strip() or self.fingerprint(source) != fingerprint
                    or len(text.encode()) >= len(json.dumps(source, ensure_ascii=False).encode())):
                return False
            self.journal._append(self.journal.events(), "scoped_event", {"scope": self.scope,
                "kind": "compaction", "data": dict(start=start, end=end, text=text, model=model, usage=usage)})
            return True

    def read_range(self, start=1, limit=20):
        if start < 1 or not 1 <= limit <= 100:
            raise ValueError("invalid history range")
        return [e for e in self.events() if e["seq"] >= start][:limit]

    def search(self, query, limit=10):
        import json
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("invalid history search")
        result = []
        for e in self.events():
            if e["kind"] == "message":
                data = {k: v for k, v in e["data"].items() if k != "continuation"}
                if query.casefold() in json.dumps(data, ensure_ascii=False).casefold():
                    result.append({**e, "data": data})
        return result[-limit:]

    def flush(self):
        self.journal.flush()
