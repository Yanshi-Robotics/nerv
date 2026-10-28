"""Stable entry point: the active installation changes only after successful validation."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ.get("NERV_HOME", Path.home() / ".nerv"))
if (root / "update-pending.json").exists() and sys.argv[1:] != ["rollback"]:
    raise SystemExit("An update was interrupted. Run nerv rollback before starting NERV.")
record_path = root / "active.json"
if (root / "update-pending.json").exists():
    pending = json.loads((root / "update-pending.json").read_text(encoding="utf-8"))
    record_path = Path(pending["snapshot"]) / "active.json"
record = json.loads(record_path.read_text(encoding="utf-8"))
python = record["python"]
if (root / "update-pending.json").exists():
    # Recovery must not depend on the old host knowing the new transaction format.
    os.execv(python, [python, str(root / "bootstrap/install.py"), "--rollback"])
os.execv(python, [python, "-I", "-m", "nerv.platform.cli", *sys.argv[1:]])
