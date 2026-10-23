# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock==4.2.4"]
# ///
"""Release installation shared by the CLI, operator UI and bootstrap script.

Only official manifests select executable packages. Original training environments,
checkpoints and calibration files are never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile

RELEASE_BASE = "https://github.com/Yanshi-Robotics/nerv/releases/latest/download"
PYTHON_VERSION = "3.12"
NETWORK_TIMEOUT_S = 60
POLL_S = 0.2


def home():
    return Path(os.environ.get("NERV_HOME", Path.home() / ".nerv"))


def python_at(directory):
    return Path(directory) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def fetch(url, target, cancelled=lambda: False, progress=lambda **_: None):
    with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT_S) as source, Path(target).open("wb") as out:
        total = int(source.headers.get("Content-Length", 0))
        count = 0
        while chunk := source.read(1024 * 1024):
            if cancelled():
                raise InterruptedError("Installation cancelled")
            out.write(chunk)
            count += len(chunk)
            progress(downloaded=count, download_total=total or None)


def manifest_path():
    for path in (home() / "release.json", Path(__file__).with_name("release.json"),
                 Path(__file__).resolve().parents[2] / "temp/release/release.json"):
        if path.exists():
            return path
    raise ValueError("No release bundle is installed. Build or install an official release first.")


def read_manifest(path=None):
    path = Path(path or manifest_path())
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or not value.get("version") or "host" not in value.get("products", {}):
        raise ValueError("Unsupported release manifest")
    return value, path.parent


def uv_executable():
    managed = home() / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    result = str(managed) if managed.is_file() else shutil.which("uv")
    if not result:
        raise ValueError("Run the NERV installer to install managed uv")
    return result


def run(command, env, cancelled, progress):
    # The caller's log contains package-manager output; credentials are absent.
    process = subprocess.Popen(command, env=env)
    try:
        while process.poll() is None:
            if cancelled():
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise InterruptedError("Installation cancelled")
            time.sleep(POLL_S)
        if process.returncode:
            raise RuntimeError(f"Installation command failed ({process.returncode}); see installation log")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


def unpack(archive, destination):
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or info.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("Unsafe module archive member")
        bundle.extractall(destination)


def install(product="host", *, manifest=None, cancelled=lambda: False, progress=lambda **_: None):
    value, source = read_manifest(manifest)
    if product not in value["products"]:
        raise ValueError("Unknown official installation module")
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    from filelock import FileLock, Timeout
    lock = FileLock(root / "installation-process.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError("Another installation is running; wait or cancel it first")
    owner = root / "installation-owner.json"
    write_json(owner, {"pid": os.getpid(), "product": product})
    try:
        spec = value["products"][product]
        stage = root / "envs" / product / (value["version"] + "-" + uuid.uuid4().hex[:8])
        cache = root / ".cache/releases" / value["version"]
        cache.mkdir(parents=True, exist_ok=True)
        assets = {}
        selected_files = list(spec["files"])
        if product == "host":
            # Supplied bundles must remain usable after the extracted directory
            # is moved. Cache their optional assets without installing modules.
            selected_files += [name for name in value["files"]
                               if name not in selected_files and (source / name).is_file()]
        for name in selected_files:
            if Path(name).name != name:
                raise ValueError("Invalid release asset name")
            receipt = value["files"][name]
            target = cache / name
            local = source / name
            progress(phase="download", asset=name)
            if local.exists():
                if local.resolve() != target.resolve():
                    shutil.copyfile(local, target)
            elif not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != receipt["sha256"]:
                url = value["base_url"].rstrip("/") + "/" + name
                if not url.startswith("https://github.com/Yanshi-Robotics/nerv/releases/"):
                    raise ValueError("Module download must use the official release origin")
                temporary = target.with_suffix(target.suffix + ".partial")
                fetch(url, temporary, cancelled, progress)
                os.replace(temporary, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != receipt["sha256"]:
                raise ValueError(f"Release checksum mismatch: {name}")
            assets[name] = target
        if cancelled():
            raise InterruptedError("Installation cancelled")
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY"}}
        env.update(NERV_HOME=str(root), PYTHONUTF8="1", UV_CACHE_DIR=str(root / ".cache/uv"), UV_CREDENTIALS_DIR=str(root / ".cache/uv-credentials"), UV_PYTHON_INSTALL_DIR=str(root / "python"))
        uv = uv_executable()
        progress(phase="environment")
        run([uv, "venv", "--python", PYTHON_VERSION, str(stage)], env, cancelled, progress)
        python = str(python_at(stage))
        progress(phase="dependencies")
        requirements = [str(assets[n]) + spec.get("extras", {}).get(n, "") for n in spec["packages"]]
        command = [uv, "pip", "install", "--python", python, *requirements]
        if spec.get("lock"):
            command = [uv, "pip", "install", "--python", python, "--require-hashes", "--only-binary=:all:",
                       "--find-links", str(cache), "-r", str(assets[spec["lock"]])]
        run(command, env, cancelled, progress)
        run([uv, "pip", "check", "--python", python], env, cancelled, progress)
        record = {"provider": spec.get("provider", product), "python": python, "label": spec["label"],
                  "version": value["version"], "product": product}
        if spec.get("archive"):
            progress(phase="assets")
            module_root = stage / "module"
            unpack(assets[spec["archive"]], module_root)
            for path in module_root.glob("worlds/*/world.yaml"):
                data = json.loads(path.read_text(encoding="utf-8"))
                data.update(python=python, assets_root=str(module_root / "assets"))
                write_json(path, data)
            for path in module_root.glob("bodies/*/body.yaml"):
                data = json.loads(path.read_text(encoding="utf-8"))
                for binding in data["buses"].values():
                    binding["python"] = python
                write_json(path, data)
            record["registry_root"] = str(module_root)
            progress(phase="verification")
            run([python, "-m", "nerv.runtime.module_check", str(module_root)], env, cancelled, progress)
        else:
            progress(phase="verification")
            module = "nerv.platform.cli" if product == "host" else "nerv_runtime_sdk.worker"
            run([python, "-m", module, "--version" if product == "host" else "discover"], env, cancelled, progress)
        if cancelled():
            raise InterruptedError("Installation cancelled")
        write_json(stage / "release.json", value)
        write_json(stage / "nerv-install.json", record)
        if product == "host":
            active = root / "active.json"
            if active.exists():
                write_json(root / "previous.json", json.loads(active.read_text(encoding="utf-8")))
            write_json(active, record)
            write_json(root / "release.json", value)
        else:
            write_json(root / "runtimes" / f"{product}.json", record)
        progress(phase="complete", state="complete")
        return record
    finally:
        owner.unlink(missing_ok=True)
        lock.release()


def update(check=False):
    from filelock import FileLock
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    if check:
        return _update(check=True)
    with FileLock(str(root / "host-running.lock"), timeout=0):
        return _update()


def _update(check=False):
    root = home()
    cache = root / ".cache/releases"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "latest.json"
    fetch(RELEASE_BASE + "/release.json", target)
    value, _ = read_manifest(target)
    current = json.loads((root / "active.json").read_text()) if (root / "active.json").exists() else {}
    if check:
        return {"available_version": value["version"], "installed_version": current.get("version"),
                "update_available": value["version"] != current.get("version")}
    snapshot = root / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    snapshot.mkdir(parents=True, mode=0o700)
    for name in ("devices", "schemes", "bodies", "vlm.json", "credentials.json", "release.json"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, snapshot / name)
        elif source.exists():
            shutil.copy2(source, snapshot / name)
    # Runtime environments stay isolated. Incompatible protocol versions fail
    # closed and can be upgraded explicitly from the component page.
    return install(manifest=target)


def rollback():
    from filelock import FileLock
    root = home()
    with FileLock(str(root / "host-running.lock"), timeout=0):
        previous = root / "previous.json"
        record = json.loads(previous.read_text(encoding="utf-8"))
        python = Path(record["python"])
        if not python.is_file():
            raise ValueError("Previous installation is unavailable")
        manifest = python.parent.parent / "release.json"
        value, _ = read_manifest(manifest)
        subprocess.run([str(python), "-m", "nerv.platform.cli", "--version"], check=True)
        write_json(root / "release.json", value)
        write_json(root / "active.json", record)
        return record


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--product", default="host")
    args = parser.parse_args()
    print(json.dumps(install(args.product, manifest=args.manifest)))


if __name__ == "__main__":
    main()
