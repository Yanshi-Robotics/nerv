"""Build the history the brain sees: a token-budgeted sliding window over the session,
with every assistant tool call paired to a result (providers reject unpaired calls).
Perception entries are not resent — the current frame is attached separately.
"""
from __future__ import annotations

from ... import config
from ...brain import prompts
from ...brain.providers.base import ToolCall


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 3)


def _entry_text(m: dict) -> str:
    role = m.get("role")
    if role == "user":
        return m.get("text", "")
    if role == "assistant":
        return (m.get("text", "") or "") + "".join(str(t) for t in m.get("tool_calls", []))
    if role == "tool":
        return m.get("content", "")
    return ""


def build(messages: list[dict], token_budget: int | None = None) -> list[dict]:
    if token_budget is None:
        token_budget = config.CONTEXT_TOKEN_BUDGET
    convo = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]
    kept: list[dict] = []
    total = 0
    for m in reversed(convo):
        t = _est_tokens(_entry_text(m))
        if total + t > token_budget and kept:
            break
        kept.append(m)
        total += t
    kept.reverse()
    kept = _sanitize_pairs(kept)
    out: list[dict] = []
    for m in kept:
        role = m["role"]
        if role == "user":
            out.append({"role": "user", "text": m["text"]})
        elif role == "assistant":
            tcs = [ToolCall(t["id"], t["name"], t.get("arguments", {})) for t in m.get("tool_calls", [])]
            out.append({"role": "assistant", "text": m.get("text", ""), "tool_calls": tcs})
        elif role == "tool":
            out.append({"role": "tool", "id": m["id"], "name": m["name"], "content": m["content"]})
    return out


def _sanitize_pairs(kept: list[dict]) -> list[dict]:
    out: list[dict] = []
    i = 0
    while i < len(kept) and kept[i].get("role") == "tool":
        i += 1
    while i < len(kept):
        m = kept[i]
        out.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            want = [(t["id"], t["name"]) for t in m["tool_calls"]]
            got: set = set()
            j = i + 1
            while j < len(kept) and kept[j].get("role") == "tool":
                got.add(kept[j].get("id"))
                out.append(kept[j])
                j += 1
            for tid, name in want:
                if tid not in got:
                    out.append({"role": "tool", "id": tid, "name": name,
                                "content": prompts.ORPHAN_TOOL_RESULT})
            i = j
        else:
            i += 1
    return out
