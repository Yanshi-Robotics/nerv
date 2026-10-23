"""Model backends are loaded in the runtime interpreter, never in the host."""
from importlib.metadata import entry_points
from typing import Protocol

POLICY_ENTRY_POINTS = "nerv.policy_backends"


class PolicyRunner(Protocol):
    def reset(self): ...
    def action(self, observation: dict) -> dict: ...
    def close(self): ...


def backend(format_name):
    matches = list(entry_points(group=POLICY_ENTRY_POINTS, name=format_name))
    if len(matches) != 1:
        raise ValueError(f"expected one installed policy backend for {format_name!r}; found {len(matches)}")
    return matches[0].load()
