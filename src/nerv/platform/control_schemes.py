"""Versioned, operator-selected control plans. No robot-specific implementation."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import paths

# Finite defaults for newly created plans; users review them before saving.
DEFAULT_TASK_SECONDS = 120.0
DEFAULT_ACTION_BUDGET = 20


class Scheme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: "scheme_" + uuid.uuid4().hex, pattern=r"^[A-Za-z0-9_-]+$")
    revision: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=120)
    robot: str = Field(min_length=1)
    mode: Literal["sim", "real"]
    template: Literal["fixed_policy", "policy_group", "visual_direct"]
    decision_model: str | None = None
    effort: str | None = None
    actions: list[str] = Field(min_length=1)
    max_runtime_s: float = Field(default=DEFAULT_TASK_SECONDS, gt=0, le=3600, allow_inf_nan=False)
    action_budget: int = Field(default=DEFAULT_ACTION_BUDGET, ge=1, le=1000)

    @model_validator(mode="after")
    def shape(self):
        if len(set(self.actions)) != len(self.actions) or any(not x.strip() for x in self.actions):
            raise ValueError("Choose distinct actions")
        if self.template == "fixed_policy":
            if len(self.actions) != 1 or self.decision_model or self.effort:
                raise ValueError("Fixed policy requires one skill and no decision model")
            self.action_budget = 1
        elif not self.decision_model or self.decision_model == "operator":
            raise ValueError("Choose a decision model")
        return self

    def structure(self):
        return {"interaction": "agent", "decision": self.decision_model,
                "execution": list(self.actions), "template": self.template}


class SchemeStore:
    def __init__(self, root=None):
        self.root = Path(root or Path(paths.NERV_HOME) / "schemes")

    def get(self, scheme_id, revision=None):
        if not scheme_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in scheme_id):
            raise ValueError("Invalid control scheme ID")
        directory = self.root / scheme_id
        if revision is None:
            revisions = [int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()]
            if not revisions:
                raise ValueError("Control scheme not found")
            revision = max(revisions)
        return Scheme.model_validate_json((directory / f"{int(revision)}.json").read_text(encoding="utf-8"))

    def list(self):
        return [self.get(p.name) for p in sorted(self.root.glob("*")) if p.is_dir()]

    def save(self, value):
        scheme = Scheme.model_validate(value)
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.root / ".lock")):
            directory = self.root / scheme.id
            if directory.exists():
                latest = self.get(scheme.id)
                if scheme.revision != latest.revision:
                    raise ValueError("Scheme changed; reload before saving")
                scheme.revision += 1
            else:
                scheme.revision = 1
            directory.mkdir(exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(scheme.model_dump_json(indent=2))
                os.replace(tmp, directory / f"{scheme.revision}.json")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return scheme


def resolve(hub, session, scheme):
    """Resolve against the live capability manifest, fail closed before control."""
    if scheme.robot != session.body or scheme.mode != session.mode:
        raise ValueError("Scheme belongs to a different robot or environment")
    from ..harness.agent import AgentHarness
    injected = getattr(hub, "_brains", {}).get(scheme.decision_model)
    if scheme.decision_model and (injected is None or isinstance(injected, AgentHarness)):
        from ..vlm.store import ModelRegistry
        try:
            ModelRegistry().require_key(scheme.decision_model)
        except KeyError as error:
            raise ValueError("Select an available decision model") from error
    robot = hub.body_client(session.body)
    if robot is None:
        raise ValueError("Connect the robot before checking this scheme")
    operations = {op.name: op for op in robot.capabilities().operations}
    kind = "primitive" if scheme.template == "visual_direct" else "skill"
    for action in scheme.actions:
        if action not in operations or operations[action].kind != kind:
            raise ValueError(f"Unavailable or incompatible action: {action}")
    fingerprints = {}
    body = hub.registry.bodies.get(session.body)
    for action in scheme.actions:
        skill = (body.skills if body else {}).get(action, {})
        if hasattr(skill, "model_dump"):
            skill = skill.model_dump()
        if skill.get("manifest"):
            manifest = json.loads(Path(skill["manifest"]).read_text(encoding="utf-8"))
            fingerprints[action] = manifest.get("sha256", {})
    return {**scheme.model_dump(), "structure": scheme.structure(), "policy_fingerprints": fingerprints}
