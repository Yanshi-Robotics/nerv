"""Package official module assets and checksums after building wheels. Never publishes."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "temp/release"


def write_member(archive, name, content):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


def bundle(output=None):
    OUT = Path(output) if output is not None else ROOT / "temp/release"
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from nerv._version import __version__
    OUT.mkdir(parents=True, exist_ok=True)
    versions = {"nerv": __version__}
    for package in ("nerv-runtime-sdk", "nerv-runtime-lerobot"):
        metadata = tomllib.loads((ROOT / "packages" / package / "pyproject.toml").read_text())
        versions[package.replace("-", "_")] = metadata["project"]["version"]
    wheels = []
    for package, version in versions.items():
        matches = list((OUT / "wheels").glob(f"{package}-{version}-*.whl"))
        if len(matches) != 1:
            raise ValueError(f"Expected one {package} {version} wheel, found {len(matches)}")
        wheels.extend(matches)
    names = {}
    for path in wheels:
        shutil.copy2(path, OUT / path.name)
        names[path.name.split("-", 1)[0]] = path.name
    archive = OUT / "so101-sim.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        # Environments contain capabilities, not versioned scene content. Assets
        # can update independently without reinstalling MuJoCo or Python.
        simulation = json.loads((ROOT / 'release/simulation.json').read_text())
        body_ids = simulation['bodies']
        for body_id in body_ids:
            body = yaml.safe_load((ROOT / "bodies" / body_id / "body.yaml").read_text())
            body["buses"] = {"sim": body["buses"]["sim"]}
            body["skills"] = {}
            write_member(z, f"bodies/{body_id}/body.yaml", json.dumps(body))
        write_member(z, 'module.json', json.dumps(simulation))
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
            ("lerobot0.6.1", "real", f"nerv-runtime-lerobot=={versions['nerv_runtime_lerobot']}")):
        source = OUT / f"{lockname}.in"
        source.write_text(requirement + "\n")
        import os
        env = dict(os.environ, UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(OUT / "uv-cache")), UV_CREDENTIALS_DIR=str(OUT / "uv-credentials"))
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
        if name == 'bootstrap.py':
            import base64
            embedded = "import sys, types, base64\nfrom pathlib import Path\nsys.path[:0] = [str(p) for p in (Path(__file__).parent / 'seed').glob('*.whl')]\n"
            for module_name in ('install_system', 'install_downloads', 'install_plan', 'managed'):
                code = (ROOT / 'src/nerv' / f'{module_name}.py').read_bytes()
                encoded = base64.b64encode(code).decode()
                embedded += f"_m = types.ModuleType('nerv_{module_name}'); sys.modules[_m.__name__] = _m; exec(compile(base64.b64decode({encoded!r}), {module_name!r}, 'exec'), _m.__dict__)\n"
            # Future imports must precede generated module initialization.
            body = source.read_text().replace('from __future__ import annotations', '')
            (OUT / name).write_text(embedded + body)
        else:
            shutil.copy2(source, OUT / name)
    published = {filename for spec in products.values() for filename in spec["files"]}
    published.update({"bootstrap.py", "launch.py", "install.sh", "install.ps1"})
    files = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
             for p in (OUT / name for name in sorted(published))}
    value = {"schema": 1, "version": __version__, "protocol_major": 3, "config_schema": 1,
             "base_url": f"https://github.com/Yanshi-Robotics/nerv/releases/download/v{__version__}", "products": products, "files": files}
    catalog_path = ROOT / 'release/asset-catalog.json'
    if catalog_path.exists():
        from nerv.asset_catalog import entries
        value['asset_catalog'] = json.loads(catalog_path.read_text())
        entries(value)
    policy_catalog = ROOT / 'release/policy-catalog.json'
    if policy_catalog.exists():
        from nerv.policy_catalog import entries as policy_entries
        value['policy_catalog'] = json.loads(policy_catalog.read_text())
        policy_entries(value)
    (OUT / "release.json").write_text(json.dumps(value, indent=2))
    from build_download_plans import build as build_plans
    build_plans(OUT)
    from build_bootstrap_seeds import build as build_seeds
    build_seeds(OUT)
    published = set(json.loads((OUT / 'release.json').read_text())['files'])
    with zipfile.ZipFile(OUT / f"nerv-{__version__}-bundle.zip", "w", zipfile.ZIP_DEFLATED) as bundle_zip:
        for name in sorted(published | {"release.json"}):
            write_member(bundle_zip, name, (OUT / name).read_bytes())
    print(f"Release candidate: {OUT}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    bundle(parser.parse_args().output)
