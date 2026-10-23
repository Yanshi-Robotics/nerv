"""Provider-neutral request budgeting and whole-tool-group compaction."""
from __future__ import annotations

import dataclasses
import json
import math


class ContextOverflow(RuntimeError):
    pass


def estimate(value) -> int:
    """Conservative byte-based estimate, never advertised as provider token usage."""
    raw = json.dumps(value, ensure_ascii=False,
                     default=lambda v: dataclasses.asdict(v) if dataclasses.is_dataclass(v) else str(v))
    return max(1, math.ceil(len(raw.encode()) / 3))


def groups(messages: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "tool":
            raise ContextOverflow("orphan tool result in model context")
        group = [message]
        calls = message.get("tool_calls") or []
        if calls:
            ids = [c.id if hasattr(c, "id") else c["id"] for c in calls]
            got = []
            while index + 1 < len(messages) and messages[index + 1].get("role") == "tool":
                index += 1
                got.append(messages[index]["id"])
                group.append(messages[index])
            if len(ids) != len(set(ids)) or sorted(ids) != sorted(got):
                raise ContextOverflow("incomplete or duplicate tool call/result group")
        out.append(group)
        index += 1
    return out


class ContextManager:
    def __init__(self, token_budget: int, output_reserve: int, image_tokens: int):
        self.token_budget = token_budget
        self.output_reserve = output_reserve
        # Configurable conservative allowance per current image; provider usage is
        # recorded separately, since image accounting differs between vendors.
        self.image_tokens = image_tokens

    def prepare(self, system: str, history: list[dict], tools: list, images: list | None,
                memory=None, summarizer=None, cancelled=lambda: False) -> tuple[str, list[dict]]:
        groups(history)  # Validate before requesting or compacting anything.
        summary = memory.summary() if memory else None
        if summary:
            history = [m for m in history if m.get("seq", summary["end"] + 1) > summary["end"]]
            system += "\n\nSession summary (historical, not a current observation):\n" + summary["text"]

        def fits(s, h):
            return (estimate([s, h, tools]) + len(images or []) * self.image_tokens
                    + self.output_reserve <= self.token_budget)

        if fits(system, history):
            return system, history
        batches = groups(history)
        if memory is None or summarizer is None or len(batches) < 3:
            raise ContextOverflow("request exceeds the context budget; no safe old span can be compacted")
        # Retain two newest complete groups, the latest user request, and all
        # subsequent messages. No unresolved tool chain can cross this boundary.
        latest_user = max((i for i, g in enumerate(batches) if g[0].get("role") == "user"), default=0)
        split = min(len(batches) - 2, latest_user)
        source_messages = [m for g in batches[:split] for m in g]
        if not source_messages or any("seq" not in m for m in source_messages):
            raise ContextOverflow("insufficient durable history for safe compaction")
        start, end = source_messages[0]["seq"], source_messages[-1]["seq"]
        source = [e for e in memory.events() if start <= e["seq"] <= end]
        fingerprint = memory.fingerprint(source)
        text, model, usage = summarizer(source_messages, summary["text"] if summary else "")
        remaining = [m for g in batches[split:] for m in g]
        # Replace the earlier summary, rather than repeatedly injecting it.
        prefix = system.split("\n\nSession summary (historical, not a current observation):\n")[0]
        compacted = prefix + "\n\nSession summary (historical, not a current observation):\n" + text
        if not fits(compacted, remaining):
            raise ContextOverflow("compaction did not free enough context; original events retained")
        if not memory.commit_summary(start, end, text, fingerprint, model, usage, cancelled):
            raise ContextOverflow("compaction cancelled, changed, empty or not smaller")
        return compacted, remaining
