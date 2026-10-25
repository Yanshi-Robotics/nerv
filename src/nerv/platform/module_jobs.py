"""Persistent operator installation jobs; imports no device or simulation framework."""
import json
import subprocess
import sys
import uuid
import time
import os
import threading
from pathlib import Path

from .. import installation, install_system, install_plan, install_downloads

# Bound each live-log response even when package managers produce very long lines.
LOG_TAIL_BYTES = 64 * 1024
LOG_TAIL_LINES = 200
_FACTS = None
_FACTS_AT = 0.0
_FACTS_LOCK = threading.Lock()
WORKERS = {}


def system_facts(refresh=False):
    global _FACTS, _FACTS_AT
    with _FACTS_LOCK:
        if refresh or _FACTS is None or time.monotonic() - _FACTS_AT > 60:
            _FACTS = install_system.detect()
            _FACTS_AT = time.monotonic()
            install_system.atomic_json(installation.home() / 'system-profile.json', _FACTS)
        return _FACTS


def system_description(refresh=False):
    manifest, _ = installation.read_manifest()
    return {'facts': system_facts(refresh), 'settings': install_system.settings(installation.home(), public=True),
            'targets': list(install_system.TARGETS), 'installation_root': str(installation.home()),
            'plans': {key: install_plan.describe(installation.home(), manifest, key, system_facts())
                      for key in manifest['products']}}


def catalog():
    for key, process in list(WORKERS.items()):
        if process.poll() is not None:
            process.wait()
            WORKERS.pop(key, None)
    from .runtime_management import installations
    installed = {r["provider"]: r for r in installations()}
    root = installation.home() / "install-jobs"
    jobs = []
    for path in sorted(root.glob("*.json")):
        job = json.loads(path.read_text(encoding="utf-8"))
        if job["state"] == "failed" and not job.get("error_code"):
            job["error_code"] = failure_code(job["id"])
        jobs.append(job)
        if (job["state"] in ("queued", "running") and job['id'] not in WORKERS
                and time.time() - job.get("created_at", 0) > 30):
            from filelock import FileLock, Timeout
            try:
                with FileLock(str(path.with_suffix(".running")), timeout=0):
                    job.update(state="paused", message="Installer interrupted; continue to resume saved downloads")
                    installation.write_json(path, job)
            except Timeout:
                pass
    jobs.sort(key=lambda job: job.get("created_at", 0))
    manifest, _ = installation.read_manifest()
    plans = {key: install_plan.describe(installation.home(), manifest, key, system_facts())
             for key in manifest['products']} if manifest.get('download_plans') else {}
    return {"modules": [{**plans.get(key, {}), "id": key, "label": product["label"], "installed": key in installed,
                         "needs_repair": _needs_repair(key, installed),
                         "installation_root": str((installation.home() / "envs" / key).resolve()),
                         "installation_path": str(Path(installed[key]["python"]).parent.parent) if key in installed else None,
                         "download_bytes": plans.get(key, {}).get('download_bytes') if plans else sum(manifest.get("files", {}).get(n, {}).get("bytes", 0)
                                               for n in manifest["products"].get(key, {}).get("files", []))}
                        for key, product in manifest["products"].items() if key != "host"], "jobs": [{k: v for k, v in job.items() if k not in ("plan_snapshot", "release_snapshot")} for job in jobs]}


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
    from filelock import FileLock
    manifest, _ = installation.read_manifest()
    if module == "host" or module not in manifest["products"]:
        raise ValueError("Unknown official module")
    root = installation.home() / "install-jobs"
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / 'dispatch.lock', timeout=0):
        if any(job['state'] in ('queued', 'running') for job in catalog()['jobs']):
            raise ValueError('Wait for or pause the current component installation')
        snapshot = None
        if manifest.get('download_plans'):
            snapshot = install_plan.select(manifest, module, install_system.settings(installation.home()), system_facts(True))
        job_id = uuid.uuid4().hex
        installation.write_json(root / f'{job_id}.json', {'id': job_id, 'module': module, 'state': 'queued',
            'phase': 'queued', 'created_at': time.time(), 'plan_snapshot': snapshot, 'release_snapshot': manifest})
        return _spawn(job_id)


def _spawn(job_id):
    root = installation.home() / 'install-jobs'
    record_path = root / f'{job_id}.json'
    record = json.loads(record_path.read_text())
    record['owner_pid'] = os.getpid()
    installation.write_json(record_path, record)
    with (root / f'{job_id}.log').open('a', encoding='utf-8') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'nerv.platform.module_jobs', job_id],
            stdout=log, stderr=log, env=installation.installer_environment())
    WORKERS[job_id] = proc
    return {'ok': True, 'id': job_id, 'pid': proc.pid}


def resume(job_id):
    from filelock import FileLock
    path = _job_path(job_id)
    with FileLock(path.parent / 'dispatch.lock', timeout=0):
        if any(job['state'] in ('queued', 'running') for job in catalog()['jobs']):
            raise ValueError('Another installation is active')
        with FileLock(str(path.with_suffix('.running')), timeout=0):
            state = json.loads(path.read_text())
            if state['state'] not in ('paused', 'waiting', 'failed', 'cancelled'):
                raise ValueError('This installation cannot be resumed')
            for suffix in ('.cancel', '.pause'):
                path.with_suffix(suffix).unlink(missing_ok=True)
            state.update(state='queued', error_code=None, message=None, created_at=time.time())
            installation.write_json(path, state)
            return _spawn(job_id)


def _job_path(job_id):
    if not job_id or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('Invalid job ID')
    path = installation.home() / 'install-jobs' / f'{job_id}.json'
    if not path.is_file():
        raise ValueError('Unknown installation job')
    return path


def pause(job_id):
    _job_path(job_id).with_suffix('.pause').touch()
    return {'ok': True}


def shutdown():
    for job_id, proc in list(WORKERS.items()):
        if proc.poll() is None:
            pause(job_id)
    for job_id, proc in list(WORKERS.items()):
        if proc.poll() is None:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Leave the pause marker; the worker checks it between bounded operations.
                pass
        else:
            proc.wait()



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


def failure_code(job_id):
    """Classify a recorded network failure without replacing the underlying log."""
    try:
        output = log_tail(job_id)["log"].lower()
    except OSError:
        return None
    if "dns error" in output or "temporary failure in name resolution" in output:
        return "download_dns"
    if "failed to download" in output or "failed to fetch" in output:
        return "download_failed"
    return None


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
    def stopped():
        if state.get('owner_pid') and os.getppid() != state['owner_pid']:
            return 'paused'
        if path.with_suffix('.cancel').exists():
            return 'cancelled'
        if path.with_suffix('.pause').exists():
            return 'paused'
        return None
    try:
        progress(state="running", pid=os.getpid())
        manifest_path = None
        if state.get('release_snapshot'):
            manifest_path = root / f'{job_id}.manifest'
            installation.write_json(manifest_path, state['release_snapshot'])
        installation.install(state['module'], cancelled=lambda: bool(stopped()), stopped=stopped,
                             manifest=manifest_path, plan_snapshot=state.get('plan_snapshot'), progress=progress)
    except Exception as error:
        final_state = stopped() or ('waiting' if isinstance(error, install_downloads.DownloadWaiting) else
            error.state if isinstance(error, install_downloads.DownloadStopped) else
            'cancelled' if isinstance(error, InterruptedError) else 'failed')
        fields = {'state': final_state, 'message': str(error)}
        if not isinstance(error, InterruptedError):
            fields["error_code"] = failure_code(job_id)
        progress(**fields)


def work(job_id):
    from filelock import FileLock
    with FileLock(installation.home() / "install-jobs" / f"{job_id}.running", timeout=0):
        _work(job_id)


if __name__ == "__main__":
    work(sys.argv[1])
