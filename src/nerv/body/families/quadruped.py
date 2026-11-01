"""Quadruped locomotion using the same measured velocity-policy loop as bipeds.

A deployment supplies its own motion envelope and stop settings. The historical
Go2 export lacks command limits, so importing its weights does not enable motion.
"""
from __future__ import annotations

import math

from .humanoid import VelocityPolicyBody, _load_yaml


class QuadrupedBody(VelocityPolicyBody):
    family = "quadruped"

    def __init__(self, spec, bus, runner):
        from ...motion_policies import validate_contract
        import json
        from pathlib import Path

        paths = {str(item.get("policy", "")) for item in spec.get("skills", {}).values()}
        paths.discard("")
        if len(paths) != 1:
            raise ValueError("Quadruped skills require one checked policy release")
        root = Path(next(iter(paths)))
        release = _load_yaml(str(root / "release.yaml"))
        if release.get("family") != self.family:
            raise ValueError("Policy family does not match the quadruped controller")
        if release.get("robot") != spec.get("policy_robot"):
            raise ValueError("Policy robot does not match the quadruped profile")
        validate_contract(json.loads((root / "contract.json").read_text()), release)

        # Never inherit the G1's physical or stopping envelope for a quadruped.
        loco = self._locomotion_knobs(spec)
        required = ("walk_speed", "turn_rate", "max_move_m", "max_turn_deg", "settle_s",
                    "brake_margin_m", "fall_tilt_rad", "fall_height_m", "hold_settle_s",
                    "hold_still_rad_s")
        missing = [key for key in required if type(loco.get(key)) not in (int, float)
                   or not math.isfinite(loco[key]) or loco[key] <= 0]
        if missing or not loco.get("clearance"):
            raise ValueError("Quadruped deployment profile needs validated motion/stop settings: "
                             + ", ".join(missing or ["clearance"]))
        if len((spec.get("actuators") or {}).get("foot_bodies", [])) != 4:
            raise ValueError("Quadruped profile must declare four support bodies")
        super().__init__(spec, bus, runner)


def build(spec, bus, runner):
    return QuadrupedBody(spec, bus, runner)
