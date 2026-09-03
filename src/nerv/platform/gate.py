"""The safety gate: a deterministic check between the brain and the body. No model here.

Every body action passes through `decide()`. The body's own `kind` is one input to the
decision, never the thing that decides whether the decision happens. Two layers:

1. **Arming.** A session starts disarmed. While disarmed, every mutating body action is
   refused with a reason the brain can act on. Only a person, through NERV/Operator, arms it.
2. **Rules.** Explicit block / approval sets, and argument checks against what the body
   declared (numeric ranges in the tool schema). Tool-node functions are not subject to the
   arming switch — they move nothing — but still pass the block/approval sets.

Hardware-level limits (joint ranges from calibration, relative-step caps) live in the body
node's bus endpoint as well; this gate is the operator's policy, that one is physics.
"""
from __future__ import annotations

from typing import Any

from ..brain import prompts
from ..nerve.body import MUTATING_KINDS, ToolSpec

ALLOW, APPROVE, DENY = "allow", "approve", "deny"


def _range_violations(spec: ToolSpec | None, args: dict) -> list[str]:
    """Check numeric arguments against minimum/maximum declared in the tool's JSON schema."""
    if spec is None:
        return []
    props = (spec.parameters or {}).get("properties") or {}
    out: list[str] = []
    for key, val in (args or {}).items():
        p = props.get(key) or {}
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        lo, hi = p.get("minimum"), p.get("maximum")
        if lo is not None and val < lo:
            out.append(f"{key}={val} is below the declared minimum {lo}")
        if hi is not None and val > hi:
            out.append(f"{key}={val} is above the declared maximum {hi}")
    return out


class SafetyGate:
    def __init__(self, needs_approval: tuple[str, ...] = (), blocked: tuple[str, ...] = ()) -> None:
        self._needs_approval = set(needs_approval)
        self._blocked = set(blocked)

    def decide(self, name: str, args: dict, *, kind: str, origin: str, armed: bool,
               spec: ToolSpec | None = None) -> tuple[str, str]:
        """origin: 'body' | 'tool' | 'meta'. Returns (decision, reason)."""
        if name in self._blocked:
            return DENY, prompts.SAFETY_RULE_MATCHED
        if name in self._needs_approval:
            return APPROVE, prompts.SAFETY_NEEDS_APPROVAL
        if origin == "body":
            bad = _range_violations(spec, args)
            if bad:
                return DENY, prompts.SAFETY_ARG_OUT_OF_RANGE.format(detail="; ".join(bad))
            if kind in MUTATING_KINDS and not armed:
                return DENY, prompts.SAFETY_NOT_ARMED
        return ALLOW, ""

    def check(self, name: str, args: dict[str, Any], **kw) -> tuple[bool, str]:
        d, reason = self.decide(name, args, **kw)
        return d == ALLOW, reason
