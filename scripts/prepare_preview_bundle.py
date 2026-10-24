"""Make local candidate modules available without installing or activating them."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from nerv.installation import read_manifest, write_json


def prepare(bundle: Path, preview: Path):
    manifest, source = read_manifest(bundle / "release.json")
    version = manifest["version"]
    if not isinstance(version, str) or Path(version).name != version or version in (".", ".."):
        raise ValueError("Invalid candidate version")
    assets = []
    for name, receipt in manifest["files"].items():
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid candidate asset name")
        path = source / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != receipt["sha256"]:
            raise ValueError(f"Candidate checksum mismatch: {name}")
        assets.append(path)
    cache = preview / ".cache/releases" / version
    cache.mkdir(parents=True, exist_ok=True)
    for path in assets:
        target = cache / path.name
        if target.is_symlink():
            raise ValueError("Preview cache must not contain symbolic links")
        if not target.exists() or target.read_bytes() != path.read_bytes():
            shutil.copyfile(path, target)
    # Activate only the catalogue, after all local assets have been checked/copied.
    # No host pointer, credentials, sessions, devices or runtime records are changed.
    write_json(preview / "release.json", manifest)
    return [name for name in manifest["products"] if name != "host"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--preview-home", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"available_modules": prepare(args.bundle, args.preview_home)}))
