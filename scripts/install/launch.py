"""Stable entry point: the active installation changes only after successful validation."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ.get("NERV_HOME", Path.home() / ".nerv"))
record = json.loads((root / "active.json").read_text(encoding="utf-8"))
python = record["python"]
os.execv(python, [python, "-m", "nerv.platform.cli", *sys.argv[1:]])
