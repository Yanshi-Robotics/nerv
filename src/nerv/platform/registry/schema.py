"""What a body.yaml / world.yaml / tool.yaml may say. Validated on load; env-expanded."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

KIND_SIM, KIND_REAL = "sim", "real"


class LaunchSpec(BaseModel):
    module: str                       # python -m <module>
    args: list[str] = Field(default_factory=list)
    python: str = ""                  # interpreter; "" = the platform's own
    env: dict[str, str] = Field(default_factory=dict)


class BusEndpoint(BaseModel):
    kind: str                         # zmq | lerobot | unitree
    python: str = ""                  # interpreter the body node must run in for this bus
    settings: dict = Field(default_factory=dict)
    verified: bool = True             # False = interface written, never run on hardware


class SkillDecl(BaseModel):
    policy: str = ""                  # policy release directory (env-expanded)
    description: str = ""


class SensorDecl(BaseModel):
    name: str                         # camera:top
    description: str = ""
    camera: str = ""                  # for sim: the MJCF camera name
    width: int = 640
    height: int = 480


class BodySpec(BaseModel):
    name: str
    family: str                       # humanoid | arm
    label: str = ""
    version: str = "0"
    sensors: list[SensorDecl] = Field(default_factory=list)
    skills: dict[str, SkillDecl] = Field(default_factory=dict)
    actuators: dict = Field(default_factory=dict)     # family-specific (pd_mode, source, limits)
    locomotion: dict = Field(default_factory=dict)    # humanoid family knobs (speeds, caps, brake …)
    buses: dict[str, BusEndpoint] = Field(default_factory=dict)   # "sim" | "real"
    guidance: str = ""
    url: str = ""                     # attach to a running node instead of launching
    dir: str = ""                     # where the yaml lives (set by loader)

    @model_validator(mode="after")
    def _families(self):
        if self.family not in ("humanoid", "arm"):
            raise ValueError(f"unknown family {self.family!r}; known: humanoid, arm")
        for k in self.buses:
            if k not in (KIND_SIM, KIND_REAL):
                raise ValueError(f"bus key must be 'sim' or 'real', got {k!r}")
        return self


class WorldSpec(BaseModel):
    name: str
    kind: str                         # sim | real
    label: str = ""
    engine: str = ""                  # mujoco (sim only)
    assets_root: str = ""             # env-expanded
    supports: dict[str, str] = Field(default_factory=dict)    # body name → arena path (sim) or ""
    spawn: dict = Field(default_factory=dict)                  # {"module": ..., "attr": ...} or xyz/yaw
    ambient: list[SensorDecl] = Field(default_factory=list)
    physics: dict = Field(default_factory=dict)
    explore: str = ""                  # generated display manifest, relative to assets_root
    python: str = ""
    guidance: str = ""
    url: str = ""
    dir: str = ""

    @model_validator(mode="after")
    def _kind(self):
        if self.kind not in (KIND_SIM, KIND_REAL):
            raise ValueError(f"world kind must be sim or real, got {self.kind!r}")
        if self.kind == KIND_SIM and not self.engine:
            raise ValueError("a sim world must name its engine")
        return self


class ToolNodeSpec(BaseModel):
    name: str
    label: str = ""
    module: str = ""                  # python -m <module> (launch)
    python: str = ""
    url: str = ""
    guidance: str = ""
    dir: str = ""
