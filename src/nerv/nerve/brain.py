"""NERV/Brain — the messages between the platform and a brain plugin.

A brain never touches a body or a tool. It talks to the platform through a :class:`Link`:
it asks to observe, asks for a tool to be called, says something to the person, and ends
the turn. Everything the brain sends is one of the dataclasses below; everything it
receives is one too. They are plain data so the transport can change (in-process today,
a socket later) without the protocol changing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .body import ActionResult, Observation, ToolSpec

# ---- platform -> brain -------------------------------------------------------------------


@dataclass
class UserMessage:
    text: str


@dataclass
class ToolResult:
    call_id: str
    name: str
    ok: bool
    message: str
    data: dict = field(default_factory=dict)

    @classmethod
    def from_action(cls, call_id: str, name: str, r: ActionResult) -> "ToolResult":
        return cls(call_id, name, r.ok, r.message, dict(r.data or {}))


@dataclass
class Stop:
    reason: str          # "interrupt" | "steps" | "time"


# ---- brain -> platform -------------------------------------------------------------------


@dataclass
class Say:
    text: str


@dataclass
class Think:
    """The brain's decision for one step: its reasoning text and the tool calls it intends.
    Sent before the CallTool messages so the platform can record a well-formed step."""
    text: str
    tool_calls: list = field(default_factory=list)     # [CallTool]


@dataclass
class CallTool:
    call_id: str
    name: str
    arguments: dict


@dataclass
class SetRegister:
    kind: str            # "core_task" | "notes"
    value: Any


@dataclass
class EndTurn:
    pass


def to_dict(msg) -> dict:
    d = asdict(msg)
    d["type"] = type(msg).__name__
    return d


class Link(Protocol):
    """The brain's only handle on the outside. Implemented by the platform."""

    def tools(self) -> list[ToolSpec]: ...

    def system_context(self) -> dict: ...        # guidance, config, body name, registers

    def history(self) -> list[dict]: ...         # neutral history (see brain.providers.base)

    def registers(self) -> dict: ...             # {"core_task": str, "notes": list[str]}

    def observe(self) -> Observation | None: ...

    def call_tool(self, call: CallTool) -> ToolResult: ...

    def set_register(self, msg: SetRegister) -> ToolResult: ...

    def think(self, msg: Think) -> None: ...

    def say(self, msg: Say) -> None: ...

    def stop_reason(self) -> str | None: ...     # interrupt | steps | time | None


class BrainPlugin(Protocol):
    name: str
    model: str
    vision: bool

    def run_turn(self, link: Link, user: UserMessage) -> None: ...
