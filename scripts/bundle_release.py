"""Package official module assets and checksums after building wheels. Never publishes."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "temp/release"


def bundle():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from nerv._version import __version__
    OUT.mkdir(parents=True, exist_ok=True)
    wheels = sorted((OUT / "wheels").glob(f"*-{__version__}-*.whl"))
    names = {}
    for path in wheels:
        shutil.copy2(path, OUT / path.name)
        names[path.name.split("-", 1)[0]] = path.name
    archive = OUT / "so101-sim.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        body = yaml.safe_load((ROOT / "bodies/arm-lerobot-so101/body.yaml").read_text())
        body["buses"] = {"sim": body["buses"]["sim"]}
        body["skills"] = {}
        z.writestr("bodies/arm-lerobot-so101/body.yaml", json.dumps(body))
        for directory in ("robots/so101", "scenes/so101_workbench"):
            for path in sorted((ROOT / "worlds" / directory).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    z.write(path, "assets/" + str(path.relative_to(ROOT / "worlds")))
        for path in sorted((ROOT / "worlds/build").glob("so101-*")):
            z.write(path, "assets/build/" + path.name)
        for path in sorted((ROOT / "worlds").glob("so101-*/world.yaml")):
            descriptor = yaml.safe_load(path.read_text())
            descriptor["assets_root"] = "assets"
            z.writestr("worlds/" + path.parent.name + "/world.yaml", json.dumps(descriptor))
    sdk, host, device = (names[k] for k in ("nerv_runtime_sdk", "nerv", "nerv_runtime_lerobot"))
    motor_driver = next(OUT.glob("feetech_servo_sdk-1.0.0-*.whl")).name
    names["feetech_servo_sdk"] = motor_driver
    products = {
        "host": {"label": "NERV", "packages": [sdk, host], "extras": {host: "[platform]"}, "files": [sdk, host]},
        "lerobot0.6.1": {"label": "SO-101 / LeRobot", "provider": "lerobot0.6.1", "packages": [sdk, device], "files": [sdk, device, motor_driver], "verify_module": "nerv_runtime_lerobot.install_check"},
        "so101-sim": {"label": "SO-101 Simulation", "provider": "so101-sim", "packages": [sdk, host], "extras": {host: "[sim]"}, "archive": archive.name, "files": [sdk, host, archive.name]},
    }
    # Re-resolve only when explicitly requested; normal rebuilds preserve the
    # reviewed dependency versions while refreshing hashes of our own wheels.
    import subprocess
    for product, lockname, requirement in (("host", "host", f"nerv[platform]=={__version__}"),
            ("so101-sim", "sim", f"nerv[sim]=={__version__}"),
            ("lerobot0.6.1", "real", f"nerv-runtime-lerobot=={__version__}")):
        source = OUT / f"{lockname}.in"
        source.write_text(requirement + "\n")
        import os
        env = dict(os.environ, UV_CACHE_DIR=str(OUT / "uv-cache"), UV_CREDENTIALS_DIR=str(OUT / "uv-credentials"))
        subprocess.run(["uv", "pip", "compile", str(source), "--universal", "--python-version", "3.12",
                       "--constraint", str(ROOT / "release/constraints" / f"{lockname}.txt"),
                       "--generate-hashes", "--no-header", "--no-annotate", "--find-links", str(OUT),
                       "--output-file", str(OUT / f"{lockname}.lock")], check=True, env=env, stdout=subprocess.DEVNULL)
        # uv may retain a same-version local wheel hash from the previous build.
        # The build just created these three wheels; bind the lock to their bytes.
        import re
        lock_path = OUT / f"{lockname}.lock"
        lock_text = lock_path.read_text()
        for package, filename in names.items():
            normalized = package.replace("_", "-")
            digest = hashlib.sha256((OUT / filename).read_bytes()).hexdigest()
            pattern = rf"(?m)^{re.escape(normalized)}==[^\n]+ \\\n(?:[ \t]+--hash[^\n]+(?:\n|$))+"
            wheel_version = filename.split("-", 2)[1]
            replacement = f"{normalized}=={wheel_version} \\\n    --hash=sha256:{digest}\n"
            lock_text = re.sub(pattern, lambda _: replacement, lock_text)
        lock_path.write_text(lock_text)
        products[product]["lock"] = f"{lockname}.lock"
        products[product]["files"].append(f"{lockname}.lock")
    for source, name in ((ROOT / "src/nerv/installation.py", "bootstrap.py"),
                         (ROOT / "scripts/install/launch.py", "launch.py"),
                         (ROOT / "scripts/install/install.sh", "install.sh"),
                         (ROOT / "scripts/install/install.ps1", "install.ps1")):
        shutil.copy2(source, OUT / name)
    published = {filename for spec in products.values() for filename in spec["files"]}
    published.update({"bootstrap.py", "launch.py", "install.sh", "install.ps1"})
    files = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
             for p in (OUT / name for name in sorted(published))}
    value = {"schema": 1, "version": __version__, "protocol_major": 3, "config_schema": 1,
             "base_url": f"https://github.com/Yanshi-Robotics/nerv/releases/download/v{__version__}", "products": products, "files": files}
    (OUT / "release.json").write_text(json.dumps(value, indent=2))
    with zipfile.ZipFile(OUT / f"nerv-{__version__}-bundle.zip", "w", zipfile.ZIP_DEFLATED) as bundle_zip:
        for name in sorted(published | {"release.json"}):
            bundle_zip.write(OUT / name, name)
    print(f"Release candidate: {OUT}")


if __name__ == "__main__":
    bundle()
