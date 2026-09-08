"""Serve registered, generated display assets without starting a world node."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("The display asset path leaves its registered directory")
    return path


def read_manifest(spec) -> tuple[Path, dict]:
    if not spec.explore:
        raise FileNotFoundError("Display resources are not configured for this world")
    root = Path(spec.assets_root).resolve()
    path = contained(root, spec.explore)
    if not path.is_file():
        raise FileNotFoundError("Display resources have not been generated")
    manifest = json.loads(path.read_text())
    if manifest.get("scene") != spec.name or manifest.get("schema_version") != 1:
        raise ValueError("Display resources do not match this world")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("Display resources have no source verification record")
    for relative, expected in sources.items():
        source = contained(root, relative)
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("Display resources are out of date; regenerate them")
    return path.parent, manifest


def asset_path(spec, filename: str) -> Path:
    directory, manifest = read_manifest(spec)
    asset = next((a for a in manifest.get("assets", []) if a.get("path") == filename), None)
    if asset is None:
        raise FileNotFoundError("Unknown display asset")
    path = contained(directory, filename)
    if not path.is_file():
        raise FileNotFoundError("A display asset is missing; regenerate resources")
    if path.stat().st_size != asset["bytes"]:
        raise ValueError("A display asset changed; regenerate resources")
    return path
