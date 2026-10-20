"""Application capability domains, independent of provider function-call transport."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .body import ToolSpec

Domain = Literal["agent_tools", "robot_actions", "robot_tasks", "world_control", "instance_control"]
DOMAINS = ("agent_tools", "robot_actions", "robot_tasks", "world_control", "instance_control")


@dataclass(frozen=True)
class Capability:
    domain: Domain
    name: str
    description: str
    parameters: dict
    kind: str = "read"


@dataclass(frozen=True)
class CapabilityCall:
    call_id: str
    domain: Domain
    name: str
    arguments: dict = field(default_factory=dict)


class CapabilityRegistry:
    """Exact transport lookup: never trust a model-supplied domain or name prefix."""

    def __init__(self):
        self._entries: dict[str, Capability] = {}

    def register(self, item: Capability) -> str:
        if item.domain not in DOMAINS:
            raise ValueError("unknown capability domain")
        # Stable opaque wire IDs avoid provider-specific punctuation and length limits.
        import hashlib
        key = "nerv_" + hashlib.sha256(f"{item.domain}:{item.name}".encode()).hexdigest()[:24]
        if key in self._entries:
            raise ValueError(f"duplicate capability: {item.domain}/{item.name}")
        self._entries[key] = item
        return key

    def resolve(self, name: str) -> Capability:
        if name not in self._entries:
            raise ValueError("capability is not available to this role")
        return self._entries[name]

    def transport(self) -> list[ToolSpec]:
        return [ToolSpec(key, f"[{v.domain}] {v.name}: {v.description}", v.parameters, v.kind)
                for key, v in self._entries.items()]

    def describe(self) -> dict:
        from dataclasses import asdict
        return {domain: [asdict(v) for v in self._entries.values() if v.domain == domain]
                for domain in DOMAINS}
