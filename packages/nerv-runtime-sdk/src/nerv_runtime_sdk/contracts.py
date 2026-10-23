"""Transport contracts shared by independent vendor implementations."""
from __future__ import annotations

from typing import Literal, Protocol
from pydantic import BaseModel, Field

PROTOCOL_MAJOR = 3
PROTOCOL_MINOR = 0
ENTRY_POINT_GROUP = "nerv.runtime_providers"
OBSERVATION_URI = "nerv://observation"
CONFIG_URI = "nerv://config"
CAPABILITIES_URI = "nerv://capabilities"
GUIDANCE_PROMPT = "guidance"
MUTATING_KINDS = frozenset({"primitive", "skill"})
NON_MUTATING_KINDS = frozenset({"read"})


class RobotProfile(BaseModel):
    id: str
    family: str
    label: str = ""
    configuration: dict = Field(default_factory=dict)


class EnvironmentBinding(BaseModel):
    id: str
    profile_id: str
    mode: Literal["sim", "real"]
    provider: str
    world: str = ""
    configuration: dict = Field(default_factory=dict)


class PolicyArtifact(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    format: str
    path: str = ""
    root_env: str = ""
    sha256: dict[str, str] = Field(default_factory=dict)
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    conditions: str = ""


class RuntimeManifest(BaseModel):
    id: str
    version: str
    protocol_major: int = PROTOCOL_MAJOR
    protocol_minor: int = PROTOCOL_MINOR
    features: list[str] = Field(default_factory=list)
    configuration_schema: dict = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)

    def check_protocol(self) -> None:
        if self.protocol_major != PROTOCOL_MAJOR:
            raise ValueError("incompatible runtime protocol major")


class RobotRuntime(Protocol):
    def prepare(self) -> dict: ...
    def actions(self) -> list[dict]: ...
    def sensor_names(self) -> list[str]: ...
    def observe(self) -> tuple[dict, list[tuple[str, bytes]]]: ...
    def invoke(self, name: str, **arguments) -> dict: ...
    def cancel_current(self, reason: str) -> dict: ...
    def hold(self, reason: str) -> dict: ...
    def release(self) -> dict: ...
    def status(self) -> dict: ...
    def close(self) -> None: ...


class RuntimeProvider(Protocol):
    def describe(self) -> RuntimeManifest: ...
    def create(self, profile: RobotProfile, binding: EnvironmentBinding) -> RobotRuntime: ...


class BindingTransition(BaseModel):
    """Reserved contract. There is deliberately no active switch endpoint."""
    source_binding: str
    target_binding: str
    expected_epoch: str
