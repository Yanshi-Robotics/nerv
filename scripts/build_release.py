"""Build local release candidates; does not publish, start services or touch devices."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
workspace = Path(os.environ.get('NERV_BUILD_WORKSPACE', '')).resolve()
if (not os.environ.get('NERV_BUILD_WORKSPACE') or root != workspace / 'source'
        or not (workspace / 'build.json').is_file() or (root / '.git').exists()):
    raise SystemExit('Use nerv preview build --source CHECKOUT to build outside the source repository.')
output = (args.output or workspace / 'bundle').resolve()
if not output.is_relative_to(workspace) or output.is_relative_to(root):
    raise SystemExit('Release output must remain in its managed build workspace, outside the source snapshot.')
output.mkdir(parents=True, exist_ok=True)
(output / "README.md").write_text("Managed NERV release candidate. "
    "Build candidates and logs may be removed after release validation; no original models are stored here.\n")
npm = "npm.cmd" if os.name == "nt" else "npm"
npm_command = ([os.environ['NERV_BUILD_NODE'], os.environ['NERV_BUILD_NPM']]
               if os.environ.get('NERV_BUILD_NODE') and os.environ.get('NERV_BUILD_NPM') else [npm])
subprocess.run([*npm_command, "--prefix", "frontend", "run", "build"], cwd=root,
               env={**os.environ, "NERV_STATIC_EXPORT": "1", "NEXT_PUBLIC_API": ""}, check=True)
for package in ("packages/nerv-runtime-sdk", "packages/nerv-runtime-lerobot", "."):
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                    "--wheel-dir", str(output / "wheels"), package], cwd=root, check=True)
# The pinned Feetech release is source-only on PyPI. Ship its pure Python wheel
# so students need neither a compiler nor an unverified build during installation.
subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir",
                str(output), "feetech-servo-sdk==1.0.0"], cwd=root, check=True)
subprocess.run([sys.executable, str(root / "scripts/bundle_release.py"), "--output", str(output)], cwd=root, check=True)
