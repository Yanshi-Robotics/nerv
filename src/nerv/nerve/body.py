"""NERV/Body — the contract between the platform and a body node.

Data plane (what may reach the brain), carried over MCP:
  tools/list + tools/call        the body's verbs: primitives and skills
  resources/read nerv://observation   state (JSON text) followed by image blobs
  prompts/get guidance           the body's own description of itself
  resources/read nerv://config   what is configurable and what it is set to
  resources/read nerv://capabilities  family, tool kinds, sensor names (NERV extension)
Control plane (never the brain), plain HTTP: /health, /status, /config, /stream, /sensors,
/stop, /hold (emergency stop that keeps the pose), /release.

A body may describe itself; only the operator authorises it (see platform.trust).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# MCP names shared by the client (platform) and the server (body node).
OBSERVATION_URI = "nerv://observation"
CONFIG_URI = "nerv://config"
CAPABILITIES_URI = "nerv://capabilities"
GUIDANCE_PROMPT = "guidance"

# Tool kinds. `read` never changes the world; `primitive` writes one target and settles;
# `skill` starts a policy loop that runs until done, timed out or stopped.
KIND_READ = "read"
KIND_PRIMITIVE = "primitive"
KIND_SKILL = "skill"
NON_MUTATING_KINDS: frozenset[str] = frozenset({KIND_READ})
MUTATING_KINDS: frozenset[str] = frozenset({KIND_PRIMITIVE, KIND_SKILL})


@dataclass
class Observation:
    """One frame of what the body can sense: a JSON state plus named images."""
    state: dict[str, Any]
    images: list[dict] = field(default_factory=list)   # [{"name": str, "png": bytes}]

    @property
    def image_png(self) -> bytes | None:
        return self.images[0]["png"] if self.images else None


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]       # JSON Schema, object-typed
    kind: str = KIND_PRIMITIVE        # read | primitive | skill


@dataclass
class Capabilities:
    name: str
    version: str
    tools: list[ToolSpec]
    guidance: str = ""
    config: dict = field(default_factory=dict)
    family: str = ""
    sensors: list[str] = field(default_factory=list)   # e.g. ["camera:head"]


@runtime_checkable
class Body(Protocol):
    """What the platform needs from a body, however it is reached."""

    def capabilities(self) -> Capabilities: ...

    def perceive(self) -> Observation: ...

    def invoke(self, name: str, **kwargs: Any) -> ActionResult: ...
