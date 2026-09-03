"""Find and load the registry: bodies/, worlds/, tools/ (plus NERV_REGISTRY_PATHS roots)."""
from __future__ import annotations

import os

import yaml

from ... import config, paths
from .schema import BodySpec, ToolNodeSpec, WorldSpec


def _expand(obj):
    if isinstance(obj, str):
        return config.env_expand(obj)
    if isinstance(obj, list):
        return [_expand(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    return obj


def _read_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return _expand(yaml.safe_load(f) or {})


def _guidance(d: str) -> str:
    p = os.path.join(d, "guidance.md")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


_PATH_KEYS = ("assets_root",)


def _resolve(data: dict, key: str) -> None:
    v = data.get(key)
    if isinstance(v, str) and v and not os.path.isabs(v):
        data[key] = os.path.normpath(os.path.join(paths.REPO_ROOT, v))


def _resolve_paths(data: dict) -> dict:
    """Relative paths in a registry file resolve against the nerv repository root, so a
    checkout with its submodules needs no environment variable to find scenes or policies."""
    for k in _PATH_KEYS:
        _resolve(data, k)
    for sk in (data.get("skills") or {}).values():
        if isinstance(sk, dict):
            _resolve(sk, "policy")
    return data


def _roots() -> list[str]:
    extra = [p for p in os.environ.get("NERV_REGISTRY_PATHS", "").split(":") if p.strip()]
    return [paths.REGISTRY_ROOT] + extra


class Registry:
    def __init__(self, roots: list[str] | None = None) -> None:
        self.bodies: dict[str, BodySpec] = {}
        self.worlds: dict[str, WorldSpec] = {}
        self.tools: dict[str, ToolNodeSpec] = {}
        for root in (roots or _roots()):
            self._scan(os.path.join(root, "bodies"), "body.yaml", BodySpec, self.bodies)
            self._scan(os.path.join(root, "worlds"), "world.yaml", WorldSpec, self.worlds)
            self._scan(os.path.join(root, "tools"), "tool.yaml", ToolNodeSpec, self.tools)

    def _scan(self, d: str, fname: str, model, out: dict) -> None:
        if not os.path.isdir(d):
            return
        for entry in sorted(os.listdir(d)):
            p = os.path.join(d, entry, fname)
            if not os.path.isfile(p):
                continue
            data = _resolve_paths(_read_yaml(p))
            data.setdefault("name", entry)
            data["dir"] = os.path.join(d, entry)
            data.setdefault("guidance", _guidance(data["dir"]))
            spec = model(**data)
            out[spec.name] = spec       # append-only: later roots may add, never silently drop

    def body(self, name: str) -> BodySpec:
        if name not in self.bodies:
            raise KeyError(f"no body named {name!r}; known: {', '.join(self.bodies) or '(none)'}")
        return self.bodies[name]

    def world(self, name: str) -> WorldSpec:
        if name not in self.worlds:
            raise KeyError(f"no world named {name!r}; known: {', '.join(self.worlds) or '(none)'}")
        return self.worlds[name]

    def tool(self, name: str) -> ToolNodeSpec:
        if name not in self.tools:
            raise KeyError(f"no tool named {name!r}; known: {', '.join(self.tools) or '(none)'}")
        return self.tools[name]

    def summary(self) -> dict:
        from .compat import matrix
        return {"bodies": [b.model_dump(exclude={"guidance"}) for b in self.bodies.values()],
                "worlds": [w.model_dump(exclude={"guidance"}) for w in self.worlds.values()],
                "tools": [t.model_dump(exclude={"guidance"}) for t in self.tools.values()],
                "matrix": matrix(self)}
