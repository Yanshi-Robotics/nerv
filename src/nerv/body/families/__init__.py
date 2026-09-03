"""Robot families. A family writes the verbs once; buses supply the endpoint.

Each family module exposes:
    build(spec: dict, bus: MotorBus, runner: SkillRunner) -> BodyImpl
where `spec` is the body.yaml as a dict (env-expanded) and BodyImpl is the protocol in
body.node. Family names are the robot's shape: humanoid, arm (quadruped later).
"""
from __future__ import annotations

import importlib

FAMILIES = {"humanoid": "nerv.body.families.humanoid", "arm": "nerv.body.families.arm"}


def load(name: str):
    if name not in FAMILIES:
        raise KeyError(f"unknown family {name!r}; known: {', '.join(FAMILIES)}")
    return importlib.import_module(FAMILIES[name])
