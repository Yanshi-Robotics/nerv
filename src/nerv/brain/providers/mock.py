"""A brain that does not think — proves the wiring with no key and no network.

Step one of a turn: call the first tool the node itself declares as non-mutating, with
zero-valued arguments. Step two: answer in text. It never calls an action that moves the
body — not because it is clever, but because something that picks blindly has no business
touching anything.
"""
from __future__ import annotations

from ...nerve.body import NON_MUTATING_KINDS
from .base import LLMReply, ToolCall

DISCLAIMER = ("(This is the mock brain. It does not look at the picture, does not read what you "
              "said, and makes no judgement — it walks the chain end to end so you can watch it.)")
_ZERO = {"string": "", "number": 0, "integer": 0, "boolean": False, "array": [], "object": {}}


def _zero_args(schema: dict) -> dict:
    props = (schema or {}).get("properties") or {}
    return {name: _ZERO.get((props.get(name) or {}).get("type"), "")
            for name in (schema or {}).get("required") or []}


class MockLLM:
    vision = False
    model = "mock"

    def chat(self, system, history, tools, images=None) -> LLMReply:
        acted = any(m.get("role") == "tool" for m in reversed(history[-4:]))
        safe = next((t for t in tools if t.kind in NON_MUTATING_KINDS), None)
        if safe and not acted:
            return LLMReply(tool_calls=[ToolCall("mock-1", safe.name, _zero_args(safe.parameters))])
        if not tools:
            return LLMReply(text="No callable tools here, so nothing to demonstrate.\n" + DISCLAIMER)
        if safe is None:
            return LLMReply(text=(f"All {len(tools)} tools would move something and I choose at "
                                  f"random, so I call none of them.\n" + DISCLAIMER))
        return LLMReply(text=f"The chain works: I called `{safe.name}` and got an answer.\n" + DISCLAIMER)
