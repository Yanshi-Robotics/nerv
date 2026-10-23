"""Build local release candidates; does not publish, start services or touch devices."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
output = root / "temp" / "release"
output.mkdir(parents=True, exist_ok=True)
(output / "README.md").write_text("2026-10-23: standard SO-101 local release validation. "
    "Build candidates and logs may be removed after release validation; no original models are stored here.\n")
npm = "npm.cmd" if os.name == "nt" else "npm"
subprocess.run([npm, "--prefix", "frontend", "run", "build"], cwd=root,
               env={**os.environ, "NERV_STATIC_EXPORT": "1", "NEXT_PUBLIC_API": ""}, check=True)
for package in ("packages/nerv-runtime-sdk", "packages/nerv-runtime-lerobot", "."):
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                    "--wheel-dir", str(output / "wheels"), package], cwd=root, check=True)
