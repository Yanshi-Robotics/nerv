"""Build local release candidates; does not publish, start services or touch devices."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=root / "temp/release")
output = parser.parse_args().output.resolve()
output.mkdir(parents=True, exist_ok=True)
(output / "README.md").write_text("2026-10-23: standard SO-101 local release validation. "
    "Build candidates and logs may be removed after release validation; no original models are stored here.\n")
npm = "npm.cmd" if os.name == "nt" else "npm"
subprocess.run([npm, "--prefix", "frontend", "run", "build"], cwd=root,
               env={**os.environ, "NERV_STATIC_EXPORT": "1", "NEXT_PUBLIC_API": ""}, check=True)
for package in ("packages/nerv-runtime-sdk", "packages/nerv-runtime-lerobot", "."):
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--wheel-dir", str(output / "wheels"), package], cwd=root, check=True)
# The pinned Feetech release is source-only on PyPI. Ship its pure Python wheel
# so students need neither a compiler nor an unverified build during installation.
subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir",
                str(output), "feetech-servo-sdk==1.0.0"], cwd=root, check=True)
subprocess.run([sys.executable, str(root / "scripts/bundle_release.py"), "--output", str(output)], cwd=root, check=True)
