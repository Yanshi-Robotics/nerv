"""Operator-requested management in an isolated provider interpreter."""
import json
import os
from pathlib import Path
import subprocess

from .. import paths

MANAGEMENT_TIMEOUT_S = 180  # Bounded model load; no hardware work is allowed here.


def manage(python: str, provider: str, operation: str, payload: dict):
    executable = Path(python).expanduser().absolute()
    # Preserve a venv executable symlink: resolving it selects the system Python.
    if not executable.is_file():
        raise ValueError("select the runtime Python executable")
    safe = ("PATH", "HOME", "USER", "LANG", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES")
    env = {k: v for k, v in os.environ.items() if k.upper() in safe}
    env["NERV_HOME"] = paths.NERV_HOME
    env["PYTHONUTF8"] = "1"
    result = subprocess.run([str(executable), "-m", "nerv_runtime_sdk.worker", "manage", "--provider", provider,
        "--operation", operation, "--home", paths.NERV_HOME], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=MANAGEMENT_TIMEOUT_S)
    if result.returncode:
        raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "runtime management failed")
    return json.loads(result.stdout.strip().splitlines()[-1])
