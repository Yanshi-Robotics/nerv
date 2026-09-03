"""Neutral model interface. Providers translate the neutral history into their own format.

History items:
  {"role": "user", "text"}
  {"role": "assistant", "text", "tool_calls": [ToolCall]}
  {"role": "tool", "id", "name", "content"}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ... import config
from ...nerve.body import ToolSpec

MAX_TOKENS = config.MAX_TOKENS


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMReply:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] | None = None


class LLM(Protocol):
    vision: bool
    model: str

    def chat(self, system: str, history: list[dict], tools: list[ToolSpec],
             images: list[dict] | None) -> LLMReply: ...


def norm_images(images) -> list[tuple[str, bytes]]:
    if not images:
        return []
    if isinstance(images, (bytes, bytearray)):
        return [("", bytes(images))]
    return [(d.get("name", ""), d["png"]) for d in images if d.get("png")]
