# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock==4.2.4", "pyyaml>=6"]
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

try:
    from . import install_system, install_plan, install_downloads, managed
except ImportError:  # Self-contained release bootstrap embeds the same helpers.
    import nerv_install_system as install_system
    import nerv_install_plan as install_plan
    import nerv_install_downloads as install_downloads
    import nerv_managed as managed

RELEASE_BASE = "https://github.com/Yanshi-Robotics/nerv/releases/latest/download"
PYTHON_VERSION = "3.12"
NETWORK_TIMEOUT_S = 60
# Keep large ML downloads from competing in dozens of simultaneous connections.
INSTALL_DOWNLOAD_CONCURRENCY = 4
INSTALL_READ_TIMEOUT_S = 180
INSTALL_HTTP_RETRIES = 5
# Installer-only network settings; never forward model credentials or arbitrary uv flags.
INSTALL_NETWORK_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE",
                       "UV_CONCURRENT_DOWNLOADS", "UV_HTTP_CONNECT_TIMEOUT", "UV_HTTP_TIMEOUT",
                       "UV_HTTP_RETRIES", "UV_OFFLINE", "UV_NO_INDEX")
POLL_S = 0.2
CONFIG_SCHEMA = 1  # Current on-disk configuration format; unknown formats fail before activation.
# Credentials and append-only histories survive a downgrade. These versioned
# settings are restored from a snapshot; newer copies remain in its recovery archive.
RESTORE_PATHS = ("active.json", "previous.json", "release.json", "runtimes", "bodies",
                 "devices", "schemes", "artifacts", "vlm.json", "config-schema.json")


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
    executable = managed.storage_root(home()) / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    result = str(executable) if executable.is_file() else shutil.which("uv")
    if not result:
        raise ValueError("Run the NERV installer to install managed uv")
    return result


def installer_environment(base=None):
    """Share network configuration between web jobs and CLI without passing model credentials."""
    source = os.environ if base is None else base
    allowed = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"}
    allowed.update(INSTALL_NETWORK_ENV)
    env = {key: value for key, value in source.items() if key.upper() in allowed}
    defaults = {"UV_CONCURRENT_DOWNLOADS": INSTALL_DOWNLOAD_CONCURRENCY,
                "UV_HTTP_CONNECT_TIMEOUT": NETWORK_TIMEOUT_S,
                "UV_HTTP_TIMEOUT": INSTALL_READ_TIMEOUT_S, "UV_HTTP_RETRIES": INSTALL_HTTP_RETRIES}
    for key, value in defaults.items():
        env.setdefault(key, str(value))
    root = home()
    storage = managed.storage_root(root)
    preferences = install_system.settings(root)
    if preferences['proxy']:
        env.update(HTTPS_PROXY=preferences['proxy'], HTTP_PROXY=preferences['proxy'], ALL_PROXY=preferences['proxy'])
    env.update(NERV_HOME=str(root), PYTHONUTF8="1", PYTHONUNBUFFERED="1",
               NERV_STORAGE_HOME=str(storage),
               UV_CACHE_DIR=str(storage / ".cache/uv"),
               UV_CREDENTIALS_DIR=str(root / ".cache/uv-credentials"),
               UV_PYTHON_INSTALL_DIR=str(storage / "python"))
    return env


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


def install(product="host", *, manifest=None, cancelled=lambda: False, progress=lambda **_: None,
            activate=True, _lock=None, stopped=None, plan_snapshot=None):
    value, source = read_manifest(manifest)
    if product not in value["products"]:
        raise ValueError("Unknown official installation module")
    root = home()
    stopped = stopped or (lambda: 'cancelled' if cancelled() else None)
    preferences = install_system.settings(root)
    selected_plan = None
    if value.get('download_plans'):
        facts = install_system.detect()
        install_system.atomic_json(root / 'system-profile.json', facts)
        selected_plan = plan_snapshot or install_plan.select(value, product, preferences, facts)
        if not install_system.receipt_compatible({'platform': selected_plan}, facts)['compatible']:
            raise ValueError('The saved installation target no longer matches this system')
    root.mkdir(parents=True, exist_ok=True)
    from filelock import FileLock, Timeout
    storage = managed.storage_root(root)
    storage.mkdir(parents=True, exist_ok=True)
    lock = _lock or FileLock(storage / "installation-process.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError("Another installation is running; wait or cancel it first")
    owner = root / "installation-owner.json"
    write_json(owner, {"pid": os.getpid(), "product": product})
    try:
        spec = value["products"][product]
        identity = managed.environment_identity(product, spec, value['files'], selected_plan)
        stage = storage / 'envs' / product / identity
        # A prior failed attempt may have left the canonical directory unusable.
        # Reuse a verified replacement rather than producing a new retry each time.
        candidates = [stage, *sorted(stage.parent.glob(identity + '-retry-*'))]
        for candidate in candidates:
            receipt = candidate / 'nerv-install.json'
            if receipt.is_file():
                candidate_record = json.loads(receipt.read_text())
                if candidate_record.get('environment_id') == identity:
                    stage = candidate
                    break
        existing = stage / 'nerv-install.json'
        reused = existing.is_file()
        if reused:
            record = json.loads(existing.read_text())
            _verify_record(record)
        if reused and not spec.get('archive'):
            if activate:
                pointer = root / 'active.json' if product == 'host' else root / 'runtimes' / (product + '.json')
                if product == 'host' and pointer.exists():
                    current = json.loads(pointer.read_text())
                    if current.get('python') != record['python']:
                        write_json(root / 'previous.json', current)
                if product != 'host' and pointer.exists():
                    _rebind({product: json.loads(pointer.read_text())}, {product: record})
                write_json(pointer, record)
                if product == 'host':
                    write_json(root / 'release.json', value)
            progress(phase='complete', state='complete', reused=True, installation_path=str(stage))
            return record
        if stage.exists() and not reused:
            # Never overwrite an incomplete or legacy environment in place.
            stage = stage.with_name(identity + '-retry-' + uuid.uuid4().hex[:8])
        progress(installation_path=str(stage.resolve()))
        cache = storage / ".cache/releases" / value["version"]
        cache.mkdir(parents=True, exist_ok=True)
        assets = {}
        selected_files = list(spec["files"])
        if product == "host":
            # Supplied bundles must remain usable after the extracted directory
            # is moved. Cache their optional assets without installing modules.
            selected_files += [name for name in value["files"]
                               if name not in selected_files and (source / name).is_file()]
        if selected_plan and product == "host":
            # Retain bundled optional resources when the extracted candidate is moved.
            for name in selected_files:
                local = source / name
                if local.is_file() and Path(name).name == name:
                    if install_downloads.digest(local) != value['files'][name]['sha256']:
                        raise ValueError('Release checksum mismatch: ' + name)
                    if local.resolve() != (cache / name).resolve():
                        shutil.copyfile(local, cache / name)
        for name in ([] if selected_plan else selected_files):
            if Path(name).name != name:
                raise ValueError("Invalid release asset name")
            receipt = value["files"][name]
            target = cache / name
            local = source / name
            progress(phase="download", asset=name, downloaded=0, download_total=None)
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
        env = installer_environment()
        uv = uv_executable()
        if selected_plan:
            assets = install_plan.install_plan(storage, selected_plan, source, stage, env=env, preferences=preferences,
                stopped=stopped, progress=progress, run=run, uv=uv, **({'reuse': True} if reused else {}))
            python = str(python_at(stage))
        elif not reused:
            progress(phase="environment")
            run([uv, "venv", "--managed-python", "--python", PYTHON_VERSION, str(stage)], env, cancelled, progress)
            python = str(python_at(stage))
            progress(phase="dependencies")
            requirements = [str(assets[n]) + spec.get("extras", {}).get(n, "") for n in spec["packages"]]
            command = [uv, "pip", "install", "--python", python, *requirements]
            if spec.get("lock"):
                command = [uv, "pip", "install", "--python", python, "--require-hashes", "--only-binary=:all:",
                           "--find-links", str(cache), "-r", str(assets[spec["lock"]])]
            run(command, env, cancelled, progress)
        else:
            python = record['python']
        run([uv, "pip", "check", "--python", python], env, cancelled, progress)
        record = {"provider": spec.get("provider", product), "python": python, "label": spec["label"],
                  "version": value["version"], "product": product, 'environment_id': identity}
        if selected_plan:
            record['platform'] = {k: selected_plan[k] for k in ('target', 'backend', 'libc_min', 'macos_min', 'minimum_driver_major', 'minimum_compute_capability') if k in selected_plan}
            record['runtime_facts'] = install_plan.runtime_check(python, record)
        if spec.get("archive"):
            progress(phase="assets")
            archive_hash = value['files'][spec['archive']]['sha256']
            module_root = storage / 'resources' / 'modules' / archive_hash / identity
            # Bindings contain interpreter paths; each immutable environment gets
            # a separate registry while Python dependencies remain reusable.
            marker = module_root / 'nerv-module.json'
            if not marker.is_file():
                module_root.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=module_root.parent, prefix='.unpack-') as temporary:
                    extracted = Path(temporary) / 'module'
                    unpack(assets[spec['archive']], extracted)
                    if module_root.exists():
                        raise ValueError('Incomplete module resource directory; inspect it before repair')
                    os.replace(extracted, module_root)
            if not marker.is_file():
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
            write_json(marker, {'archive_sha256': archive_hash, 'environment_id': identity})
        else:
            progress(phase="verification")
            module = "nerv.platform.cli" if product == "host" else "nerv_runtime_sdk.worker"
            run([python, "-m", module, "--version" if product == "host" else "discover"], env, cancelled, progress)
            if spec.get("verify_module"):
                run([python, "-m", spec["verify_module"]], env, cancelled, progress)
        if cancelled():
            raise InterruptedError("Installation cancelled")
        if not reused:
            write_json(stage / "release.json", value)
            write_json(stage / "nerv-install.json", record)
        if state := stopped():
            raise install_downloads.DownloadStopped(state)
        if not activate:
            return record
        if product == "host":
            active = root / "active.json"
            if active.exists():
                write_json(root / "previous.json", json.loads(active.read_text(encoding="utf-8")))
            write_json(active, record)
            write_json(root / "release.json", value)
        else:
            old = root / "runtimes" / f"{product}.json"
            if old.exists():
                _rebind({product: json.loads(old.read_text(encoding="utf-8"))}, {product: record})
            write_json(root / "runtimes" / f"{product}.json", record)
        progress(phase="complete", state="complete")
        return record
    finally:
        owner.unlink(missing_ok=True)
        lock.release()


def _snapshot():
    root = home()
    destination = root / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, mode=0o700)
    for name in RESTORE_PATHS:
        source = root / name
        if source.is_dir():
            shutil.copytree(source, destination / name)
        elif source.is_file():
            shutil.copy2(source, destination / name)
    write_json(destination / "snapshot.json", {"paths": list(RESTORE_PATHS), "schema": CONFIG_SCHEMA})
    return destination


def _restore(snapshot):
    root = home()
    snapshot = Path(snapshot).resolve(strict=True)
    if not snapshot.is_relative_to((root / "backups").resolve()):
        raise ValueError("Recovery snapshot must belong to this NERV installation")
    metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    if metadata.get("paths") != list(RESTORE_PATHS):
        raise ValueError("Unsupported recovery snapshot")
    # Preserve configurations created since the update, including new policy
    # references. Never overwrite a student's later work without a recoverable copy.
    saved = snapshot / ("replaced-" + uuid.uuid4().hex[:8])
    saved.mkdir(mode=0o700)
    for name in RESTORE_PATHS:
        target, source = root / name, snapshot / name
        if target.exists():
            shutil.move(str(target), str(saved / name))
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            shutil.copy2(source, target)
    return str(saved)


def _verify_record(record):
    python = Path(record["python"])
    if not python.is_file():
        raise ValueError("Previous installation is unavailable")
    if record.get('platform'):
        compatibility = install_system.receipt_compatible(record, install_system.detect())
        if compatibility['compatible'] is False:
            raise ValueError(compatibility['reason'])
        install_plan.runtime_check(python, record)
    module = "nerv.platform.cli" if record.get("product", "host") == "host" else "nerv_runtime_sdk.worker"
    subprocess.run([str(python), "-m", module, "--version" if module.endswith("cli") else "discover"], check=True)


def _rebind(previous, records):
    # Only exact executable references change; no string replacement inside paths
    # or user-authored text. No robot or camera is opened during this migration.
    replacements = {old["python"]: records[product]["python"] for product, old in previous.items()
                    if product in records}
    import yaml
    for path in (home() / "bodies").glob("*/body.yaml"):
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        changed = False
        for binding in value.get("buses", {}).values():
            if binding.get("python") in replacements:
                binding["python"] = replacements[binding["python"]]
                changed = True
        if changed:
            write_json(path, value)


def update(check=False, *, manifest=None, legacy_python=None):
    from filelock import FileLock, Timeout
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    if check:
        return _update(check=True, manifest=manifest)
    try:
        with FileLock(str(root / "host-running.lock"), timeout=0):
            with FileLock(str(managed.storage_root(root) / "installation-process.lock"), timeout=0) as lock:
                return _update(manifest=manifest, lock=lock, legacy_python=legacy_python)
    except Timeout as exc:
        raise ValueError("Exit NERV and wait for any installation to finish, then run nerv update again.") from exc


def _update(check=False, *, manifest=None, lock=None, legacy_python=None):
    root = home()
    if (root / "update-pending.json").exists():
        raise ValueError("An interrupted update needs recovery. Exit NERV and run nerv rollback.")
    if manifest is None:
        cache = root / ".cache/releases"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / ("latest-" + uuid.uuid4().hex + ".json")
        fetch(RELEASE_BASE + "/release.json", target)
    else:
        target = Path(manifest)
    value, _ = read_manifest(target)
    current = json.loads((root / "active.json").read_text()) if (root / "active.json").exists() else {}
    if not current and legacy_python:
        version = subprocess.check_output([legacy_python, "-m", "nerv.platform.cli", "--version"], text=True).strip()
        if not version.startswith("nerv "):
            raise ValueError("Cannot identify the previous NERV installation")
        current = {"python": str(Path(legacy_python).absolute()), "version": version[5:], "product": "host", "legacy": True}
    if check:
        return {"available_version": value["version"], "installed_version": current.get("version"),
                "update_available": value["version"] != current.get("version")}
    if current.get("version") == value["version"] and manifest is None and not legacy_python:
        return {"version": value["version"], "updated": False, "message": "NERV is already up to date."}
    schema_file = root / "config-schema.json"
    schema = json.loads(schema_file.read_text()).get("schema") if schema_file.exists() else CONFIG_SCHEMA
    if schema != CONFIG_SCHEMA or value.get("config_schema", CONFIG_SCHEMA) != CONFIG_SCHEMA:
        raise ValueError("This configuration format needs a supported migration before updating")
    previous = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (root / "runtimes").glob("*.json")}
    missing = set(previous) - set(value["products"])
    if missing:
        raise ValueError("Release does not include installed components: " + ", ".join(sorted(missing)))
    snapshot = _snapshot()
    if legacy_python and not (root / "active.json").exists():
        # Retain the source-installed host as a usable rollback target.
        write_json(snapshot / "active.json", current)
    pending = root / "update-pending.json"
    write_json(pending, {"snapshot": str(snapshot), "phase": "staging"})
    try:
        records = {}
        for product in [*sorted(previous), "host"]:
            plan = None
            profile = previous.get(product, {}).get('platform')
            if profile and value.get('download_plans'):
                prefs = {**install_system.settings(root), 'target': profile['target'], 'compute': profile['backend']}
                plan = install_plan.select(value, product, prefs, install_system.detect())
            records[product] = install(product, manifest=target, activate=False, _lock=lock, plan_snapshot=plan)
        write_json(pending, {"snapshot": str(snapshot), "phase": "activating"})
        _rebind(previous, records)
        for product, record in records.items():
            if product != "host":
                write_json(root / "runtimes" / f"{product}.json", record)
        write_json(root / "config-schema.json", {"schema": CONFIG_SCHEMA})
        if current:
            write_json(root / "previous.json", {**current, "snapshot": str(snapshot)})
        write_json(root / "release.json", value)
        write_json(root / "active.json", records["host"])
        pending.unlink()
        return {**records["host"], "updated_components": sorted(previous), "snapshot": str(snapshot)}
    except BaseException:
        _restore(snapshot)
        pending.unlink(missing_ok=True)
        raise


def rollback():
    from filelock import FileLock
    root = home()
    with FileLock(str(root / "host-running.lock"), timeout=0), FileLock(str(root / "installation-process.lock"), timeout=0):
        pending = root / "update-pending.json"
        if pending.exists():
            recovery = json.loads(pending.read_text(encoding="utf-8"))
            _restore(recovery["snapshot"])
            pending.unlink()
            return {"recovered": True}
        record = json.loads((root / "previous.json").read_text(encoding="utf-8"))
        _verify_record(record)
        if record.get("snapshot"):
            snapshot = Path(record["snapshot"])
            for path in (snapshot / "runtimes").glob("*.json"):
                _verify_record(json.loads(path.read_text(encoding="utf-8")))
            saved = _restore(snapshot)
            return {**record, "preserved_newer_configuration": saved}
        # Initial bootstrap installations from older candidates have no snapshot.
        python = Path(record["python"])
        value, _ = read_manifest(python.parent.parent / "release.json")
        write_json(root / "release.json", value)
        write_json(root / "active.json", record)
        return record


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest")
    parser.add_argument("--product", default="host")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--legacy-python")
    parser.add_argument('--target', choices=['auto', *install_system.TARGETS])
    parser.add_argument('--compute', choices=['auto', 'cpu', 'cu130'])
    args = parser.parse_args()
    selection = {key: getattr(args, key) for key in ('target', 'compute') if getattr(args, key) is not None}
    if selection:
        install_system.save_settings(home(), selection)
    if args.rollback:
        print(json.dumps(rollback()))
        return
    if not args.manifest:
        parser.error("--manifest is required for installation")
    if args.update or (args.product == "host" and (home() / "active.json").exists()):
        result = update(manifest=args.manifest, legacy_python=args.legacy_python)
    elif args.product == "host":
        from filelock import FileLock
        home().mkdir(parents=True, exist_ok=True)
        with FileLock(str(home() / "host-running.lock"), timeout=0):
            result = install(args.product, manifest=args.manifest)
    else:
        result = install(args.product, manifest=args.manifest)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
