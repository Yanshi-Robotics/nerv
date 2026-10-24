"""Persistent operator installation jobs; imports no device or simulation framework."""
import json
import subprocess
import sys
import uuid
import time
import os
from pathlib import Path

from .. import installation

# Bound each live-log response even when package managers produce very long lines.
LOG_TAIL_BYTES = 64 * 1024
LOG_TAIL_LINES = 200

def catalog():
    from .runtime_management import installations
    installed = {r["provider"]: r for r in installations()}
    root = installation.home() / "install-jobs"
    jobs = []
    for path in sorted(root.glob("*.json")):
        job = json.loads(path.read_text(encoding="utf-8"))
        jobs.append(job)
        if job["state"] in ("queued", "running") and time.time() - job.get("created_at", 0) > 30:
            from filelock import FileLock, Timeout
            try:
                with FileLock(str(path.with_suffix(".running")), timeout=0):
                    job.update(state="failed", message="Installer interrupted; retry to resume verified downloads")
                    installation.write_json(path, job)
            except Timeout:
                pass
    jobs.sort(key=lambda job: job.get("created_at", 0))
    manifest, _ = installation.read_manifest()
    return {"modules": [{"id": key, "label": product["label"], "installed": key in installed,
                         "needs_repair": _needs_repair(key, installed),
                         "installation_root": str((installation.home() / "envs" / key).resolve()),
                         "installation_path": str(Path(installed[key]["python"]).parent.parent) if key in installed else None,
                         "download_bytes": sum(manifest.get("files", {}).get(n, {}).get("bytes", 0)
                                               for n in manifest["products"].get(key, {}).get("files", []))}
                        for key, product in manifest["products"].items() if key != "host"], "jobs": jobs}


def _needs_repair(key, installed):
    """Detect broken environment pointers/assets without launching hardware workers."""
    from pathlib import Path
    path = installation.home() / "runtimes" / f"{key}.json"
    if not path.exists():
        return False
    if key not in installed:
        return True
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        executable = Path(record["python"])
        stage = executable.parent.parent
        receipt = stage / "nerv-install.json"
        if not receipt.is_file():
            return True
        verified = json.loads(receipt.read_text(encoding="utf-8"))
        if verified.get("python") != record["python"] or verified.get("version") != record.get("version"):
            return True
        if record.get("registry_root") and not (Path(record["registry_root"]) / "worlds").is_dir():
            return True
        return False
    except (OSError, ValueError, KeyError):
        return True


def start(module):
    manifest, _ = installation.read_manifest()
    if module == "host" or module not in manifest["products"]:
        raise ValueError("Unknown official module")
    root = installation.home() / "install-jobs"
    root.mkdir(parents=True, exist_ok=True)
    if any(job["state"] in ("queued", "running") for job in catalog()["jobs"]):
        raise ValueError("Wait for or cancel the current component installation")
    job_id = uuid.uuid4().hex
    path = root / f"{job_id}.json"
    installation.write_json(path, {"id": job_id, "module": module, "state": "queued", "phase": "queued", "created_at": time.time()})
    from .runtime_management import environment
    with (root / f"{job_id}.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-m", "nerv.platform.module_jobs", job_id],
            stdout=log, stderr=log, env=environment())
    return {"ok": True, "id": job_id, "pid": proc.pid}


def log_tail(job_id):
    """Read only the bounded tail; mtime denotes output, not a poll heartbeat."""
    if not job_id or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid installation job ID")
    path = installation.home() / "install-jobs" / f"{job_id}.log"
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - LOG_TAIL_BYTES)
        stream.seek(offset)
        data = stream.read(LOG_TAIL_BYTES)
        modified = os.fstat(stream.fileno()).st_mtime
    # A tail may start inside a UTF-8 character or a partial line.
    lines = data.decode("utf-8", errors="replace").splitlines()
    if offset and len(lines) > 1:
        lines = lines[1:]
    return {"log": "\n".join(lines[-LOG_TAIL_LINES:]), "bytes": size,
            "modified_at": modified if size else None,
            "truncated": offset > 0 or len(lines) > LOG_TAIL_LINES}


def cancel(job_id):
    if not job_id or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid job ID")
    root = installation.home() / "install-jobs"
    if not (root / f"{job_id}.json").exists():
        raise ValueError("Unknown installation job")
    (root / f"{job_id}.cancel").touch()
    return {"ok": True}


def _work(job_id):
    root = installation.home() / "install-jobs"
    path = root / f"{job_id}.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    def progress(**fields):
        now = time.time()
        if fields.get("phase", state["phase"]) != state["phase"]:
            state.update(phase_started_at=now, downloaded=None, download_total=None, asset=None)
        state.update(updated_at=now)
        state.update(fields)
        installation.write_json(path, state)
    try:
        progress(state="running", pid=os.getpid())
        installation.install(state["module"], cancelled=lambda: (root / f"{job_id}.cancel").exists(), progress=progress)
    except Exception as error:
        progress(state="cancelled" if isinstance(error, InterruptedError) else "failed", message=str(error))


def work(job_id):
    from filelock import FileLock
    with FileLock(installation.home() / "install-jobs" / f"{job_id}.running", timeout=0):
        _work(job_id)


if __name__ == "__main__":
    work(sys.argv[1])
