"""World-owned schemas and isolated asset generation. No scene knowledge lives here."""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

from jsonschema import validate


def resolve_configuration(descriptor, supplied):
    schema = descriptor.get("schema", {"type": "object", "properties": {}, "additionalProperties": False})
    values = {key: field["default"] for key, field in schema.get("properties", {}).items() if "default" in field}
    values.update(supplied)
    validate(values, schema)
    return values


def build_instance(world, request_path):
    request = json.loads(Path(request_path).read_text())
    values = resolve_configuration(world.configuration, request["values"])
    builder = world.configuration.get("builder")
    if not builder:
        if values:
            raise ValueError("This world has no configuration builder")
        return None, {"configuration": values}
    # Installed world registrations are trusted executable plugins, never model-supplied paths.
    root = Path(world.assets_root).resolve()
    sys.path.insert(0, str(root))
    output = Path(request_path).parent / "scene.xml"
    function = getattr(importlib.import_module(builder["module"]), builder["function"])
    function(output=output, values=values, **builder.get("arguments", {}))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt = {"configuration": values, "arena_sha256": digest,
               "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                 for name in builder.get("source_files", [])},
               "descriptor_sha256": hashlib.sha256(json.dumps(world.configuration, sort_keys=True).encode()).hexdigest()}
    (output.parent / "receipt.json").write_text(json.dumps(receipt, indent=2))
    return str(output), receipt
