"""Seed an isolated POSIX preview with an explicit snapshot of saved models and keys.

Run with the repository Python environment. No network requests are made. Existing
preview model files are never overwritten; sessions and device state are not copied.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from nerv.vlm.store import CredentialStore, ModelRegistry, PROVIDERS, private_write


def seed(source: Path, destination: Path) -> dict:
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if os.name == "nt":
        raise ValueError("This local preview helper currently supports POSIX credential files only")
    if source == destination or source.is_relative_to(destination):
        raise ValueError("The preview must be separate from the source home")
    if destination.is_relative_to(source) and not (
        destination.parent == source / "profiles" and not (source / "profiles").is_symlink()
    ):
        raise ValueError("Use a separate home or a named child of the profiles directory")
    names = ("credentials.json", "vlm.json")
    if any((destination / name).exists() or (destination / name).is_symlink() for name in names):
        raise ValueError("Preview model configuration already exists; refusing to overwrite it")

    # Validate all input before writing. Never print raw JSON or validation errors:
    # user-defined model fields and credential values may contain private content.
    registry = ModelRegistry(source)
    data = registry._read()
    models = {name: registry.model(name).model_dump() for name in data["models"]}
    keys = CredentialStore(source)._read()
    references = {reference for _, reference, _ in PROVIDERS.values()}
    keys = {ref: value for ref, value in keys.items() if ref in references and value}
    if not keys or any(not isinstance(value, str) or not value.strip() for value in keys.values()):
        raise ValueError("No usable registered credentials were found")
    config = {"models": models, "efforts": data.get("efforts", {}),
              "selection": data.get("selection", "")}
    # Preserve test-home history, but never import checks, physical acceptance,
    # device profiles, trust grants, sessions, install pointers or environment files.
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(destination / "vlm.json", config)
    private_write(destination / "credentials.json", keys)
    return {"models": len(models), "credential_providers": len(keys)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-home", type=Path, required=True)
    parser.add_argument("--preview-home", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = seed(args.source_home, args.preview_home)
    except Exception:
        # Do not render exceptions that may embed a credential or model payload.
        parser.exit(1, "Import failed. Check distinct homes, private source credentials, "
                    "valid model settings, and an empty preview model configuration. "
                    "Existing files are not overwritten.\n")
    print(json.dumps({"imported": result, "sessions_copied": False, "source_modified": False}))


if __name__ == "__main__":
    main()
