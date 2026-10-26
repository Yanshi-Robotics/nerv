"""Offline student setup and artifact import, executed in the adapter environment."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from .standard import PRODUCT, CHANNELS, CAMERAS, UNITS, validate_cameras, validate_manifest, validate_calibration


FIELDS = {
    "configure": {"port": "Serial port", "id": "Robot ID", "calibration_dir": "Calibration directory",
                  "camera_config_file": "Camera configuration file", "policy_device": "Inference device"},
    "import": {"path": "Checkpoint directory", "id": "Policy ID", "conditions": "Demonstrated task and scene",
               "standard_declared": "I trained with the standard SO-101 course convention"},
    "inspect": {"id": "Policy ID"},
    "check": {"id": "Policy ID", "device": "Inference device"},
}


def describe():
    descriptor = {"product": PRODUCT, "label": "Standard SO-101 / LeRobot", "operations": {
        op: {"type": "object", "properties": {k: {"type": "boolean" if k == "standard_declared" else "string", "title": title}
                                                for k, title in fields.items()},
             "required": list(fields), "additionalProperties": False} for op, fields in FIELDS.items()}}
    for operation, key in (("configure", "policy_device"), ("check", "device")):
        descriptor["operations"][operation]["properties"][key].update(enum=["cpu", "cuda", "mps"], default="cpu")
    from .prerequisites import CAPABILITY
    descriptor["prerequisites"] = CAPABILITY
    descriptor["camera_preview_module"] = "nerv_runtime_lerobot.setup_camera"
    return descriptor


def _id(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise ValueError("ID must contain letters, digits, underscores or hyphens")
    return value


def _write(path, value):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inspect_checkpoint(path, declared):
    root = Path(path).expanduser().resolve(strict=True)
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    if config.get("type") != "act":
        raise ValueError("first release accepts native LeRobot ACT checkpoints")
    if config.get("output_features", {}).get("action", {}).get("shape") != [len(CHANNELS)]:
        raise ValueError("expected six standard position actions")
    if not declared:
        raise ValueError("confirm the standard course convention; shapes alone cannot establish units or order")
    manifest = {"id": "inspection", "product": PRODUCT, "format": "lerobot-act", "sdk_version": "0.6.1",
                "path": str(root), "state_names": list(CHANNELS), "action_names": list(CHANNELS),
                "image_names": list(CAMERAS), "position_units": UNITS,
                "input_features": config["input_features"], "inputs": config["input_features"],
                "outputs": config["output_features"], "semantics_source": "user_standard_declaration"}
    validate_manifest(manifest)
    files = {"config.json", "model.safetensors", "policy_preprocessor.json", "policy_postprocessor.json"}
    # Processor configs refer to their tensor state. Preserve and fingerprint each
    # reference; do not require unrelated training logs or invent normalization.
    def references(value):
        if isinstance(value, dict):
            for v in value.values():
                references(v)
        elif isinstance(value, list):
            for v in value:
                references(v)
        elif isinstance(value, str) and value.endswith(".safetensors"):
            files.add(value)
    for filename in ("policy_preprocessor.json", "policy_postprocessor.json"):
        references(json.loads((root / filename).read_text(encoding="utf-8")))
    hashes = {}
    for name in sorted(files):
        target = (root / name).resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError("processor references a file outside the checkpoint")
        with target.open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest["sha256"] = hashes
    return manifest


def artifact_stamp(manifest):
    """Cheap display invalidation; execution still verifies every file hash."""
    root = Path(manifest["path"])
    return {name: [p.stat().st_size, p.stat().st_mtime_ns]
            for name in manifest["sha256"] for p in [root / name]}


def manage(operation, payload, home):
    from filelock import FileLock
    root = Path(home).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / "runtime-management.lock"):
        return _manage(operation, payload, home)


def _manage(operation, payload, home):
    import jsonschema
    if operation == "describe":
        return describe()
    if operation == "invalidate_prerequisites":
        _write(Path(home) / "devices/prerequisites.json", {"state": "unknown", "code": "check_required"})
        return {"ok": True}
    if operation == "prerequisite_status":
        from .prerequisites import status
        return status(payload, home)
    if operation == "environment":
        from .prerequisites import inference_device
        return {"policy_device": inference_device()}
    if operation == "scheme_options":
        from .action_catalog import primitive_actions
        profile = json.loads(Path(__file__).with_name("product.json").read_text())
        actions = primitive_actions(profile["buses"]["real"]["settings"])
        actions += [{"name": p["id"], "description": p.get("conditions", ""), "kind": "skill"}
                    for p in _manage("policies", {}, home)["policies"]]
        return {"actions": [a for a in actions if a["kind"] in ("skill", "primitive")]}
    if operation == "discover":
        from .discovery import discover
        result = discover(home)
        _write(Path(home) / "devices/last-check.json", {
            "at": time.time(), "repair_step": result.get("repair_step"),
            "connected": False,
        })
        return result
    if operation == "calibrations":
        directory = Path(payload["directory"]).expanduser().resolve(strict=True)
        return {"calibrations": [{"id": p.stem, "directory": str(directory)}
                                 for p in sorted(directory.glob("*.json")) if p.is_file()]}
    if operation == "validate_calibration":
        calibration = Path(payload["calibration_dir"]).expanduser().resolve(strict=True)
        robot_id = _id(payload["id"])
        target = calibration / f"{robot_id}.json"
        if not target.is_file():
            candidates = [p.stem for p in calibration.glob("*.json")]
            raise ValueError("No calibration for this robot ID. Available IDs: " + ", ".join(candidates))
        value = json.loads(target.read_text(encoding="utf-8"))
        validate_calibration(value)
        return {"ok": True, "id": robot_id, "connected": False}
    if operation == "configure_standard":
        from .prerequisites import require_current, inference_device
        require_current(payload, home)
        payload = {**payload, "policy_device": inference_device()}
        from .discovery import discover
        root = Path(home)
        cameras = payload["cameras"]
        validate_cameras(cameras)
        calibration = Path(payload["calibration_dir"]).expanduser().resolve(strict=True)
        robot_id = _id(payload["id"])
        if not (calibration / f"{robot_id}.json").is_file():
            raise ValueError("selected calibration directory has no file for this robot ID")
        if not payload["port"].strip() or payload.get("policy_device", "cpu") not in ("cpu", "cuda", "mps"):
            raise ValueError("provide a port and select cpu, cuda or mps")
        camera_digest = hashlib.sha256(json.dumps(cameras, sort_keys=True).encode()).hexdigest()[:16]
        camera_file = root / "devices" / f"cameras-{camera_digest}.json"
        _write(camera_file, cameras)
        request = {k: payload[k] for k in ("port", "id", "calibration_dir")}
        request.update(camera_config_file=str(camera_file), policy_device=payload.get("policy_device", "cpu"))
        result = _manage("configure", request, home)
        import yaml
        profile_path = root / "bodies/arm-lerobot-so101/body.yaml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        found = discover(home)
        identity = next((p for p in found["ports"] if p["port"] == payload["port"]), {})
        camera_identities = {name: next((c.get("identity") for c in found["cameras"]
            if str(c["device"]) == str(cfg["index_or_path"]) and c.get("backend") == cfg.get("backend")), None)
            for name, cfg in cameras.items()}
        profile["buses"]["real"]["settings"]["camera_identities"] = camera_identities
        profile["buses"]["real"]["settings"]["device_identity"] = identity
        _write(profile_path, profile)
        _write(root / "devices/standard.json", {**request, "cameras": cameras, "identity": identity, "camera_identities": camera_identities})
        return result
    if operation == "policies":
        records = []
        for path in sorted((Path(home) / "artifacts").glob("*/artifact.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            item = {k: value.get(k) for k in ("id", "conditions", "format", "product", "semantics_source")}
            receipt = path.with_name("check.json")
            item["check"] = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {"state": "unchecked"}
            try:
                if item["check"].get("state") == "loaded" and item["check"].get("files") != artifact_stamp(value):
                    item["check"] = {"state": "changed"}
            except OSError:
                item["check"] = {"state": "changed"}
            item["check"].pop("files", None)
            records.append(item)
        return {"policies": records}
    if operation not in FIELDS:
        raise ValueError("unknown management operation")
    jsonschema.validate(payload, describe()["operations"][operation])
    root = Path(home).expanduser().resolve()
    body_file = root / "bodies" / "arm-lerobot-so101" / "body.yaml"
    if operation == "configure":
        from .prerequisites import require_current, inference_device
        require_current(payload, home)
        if payload["policy_device"] != inference_device():
            raise ValueError("Inference device does not match the verified runtime environment")
        import yaml
        from importlib.resources import files
        camera_path = Path(payload["camera_config_file"]).expanduser().resolve(strict=True)
        validate_cameras(json.loads(camera_path.read_text(encoding="utf-8")))
        calibration = Path(payload["calibration_dir"]).expanduser().resolve(strict=True)
        robot_id = _id(payload["id"])
        if not (calibration / f"{robot_id}.json").is_file():
            raise ValueError("selected calibration directory has no file for this robot ID")
        if not payload["port"].strip() or payload["policy_device"] not in ("cpu", "cuda", "mps"):
            raise ValueError("provide a port and select cpu, cuda or mps")
        template = json.loads(files("nerv_runtime_lerobot").joinpath("product.json").read_text())
        if body_file.exists():
            existing = yaml.safe_load(body_file.read_text(encoding="utf-8"))
            template["skills"] = existing.get("skills", {})
            template["buses"] = {**existing.get("buses", {}), **template["buses"]}
        binding = template["buses"]["real"]
        binding["python"] = sys.executable
        binding["settings"].update({**payload, "id": robot_id, "camera_config_file": str(camera_path),
                                   "calibration_dir": str(calibration)})
        # YAML accepts JSON; an atomic JSON write avoids a second serializer.
        _write(body_file, template)
        return {"ok": True, "product": PRODUCT, "connected": False, "profile": str(body_file)}
    policy_id = _id(payload["id"])
    record = root / "artifacts" / policy_id / "artifact.json"
    if operation == "import":
        if not body_file.exists():
            raise ValueError("configure the standard device before importing a policy")
        manifest = inspect_checkpoint(payload["path"], payload["standard_declared"])
        manifest.update(id=policy_id, conditions=payload["conditions"])
        import yaml
        profile = yaml.safe_load(body_file.read_text(encoding="utf-8"))
        if policy_id in profile.get("skills", {}) or record.exists():
            raise ValueError("policy ID already exists; import a new version with a new ID")
        if policy_id in {"read_state", "move_joints", "nudge_joints", "set_gripper", "wait_execution", "cancel_execution", "execution_status"}:
            raise ValueError("policy ID is reserved by device controls")
        _write(record, manifest)
        profile.setdefault("skills", {})[policy_id] = {"artifact": policy_id, "manifest": str(record), "modes": ["real"],
            "description": f"Run the imported policy. Demonstrated conditions: {payload['conditions']}"}
        _write(body_file, profile)
        return {"ok": True, "manifest": manifest, "connected": False}
    runner = None
    try:
        manifest = json.loads(record.read_text(encoding="utf-8"))
        validate_manifest(manifest)
        from .policy import artifact_files
        artifact_files(manifest)
        if operation == "inspect":
            return {"ok": True, "manifest": manifest}
        from nerv_runtime_sdk.policies import backend
        runner = backend(manifest["format"])(manifest, payload["device"])
        import numpy as np
        import math
        from .standard import WIDTH, HEIGHT
        # A synthetic tensor probe checks the complete native processor chain.
        # It is explicitly not a camera observation or evidence of task success.
        raw = {key: 0.0 for key in CHANNELS}
        raw.update({key: np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8) for key in CAMERAS})
        runner.reset()
        action = runner.action(raw)
        if (not isinstance(action, dict) or set(action) != set(CHANNELS)
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in action.values())):
            raise ValueError("ACT must produce six finite standard position targets")
        _write(record.with_name("check.json"), {"state": "loaded", "device": payload["device"],
               "checked_at": time.time(), "files": artifact_stamp(manifest), "inference": True,
               "observation_source": "synthetic_contract_probe", "hardware_verified": False})
        return {"ok": True, "loaded": True, "inference": True, "device_write": False,
                "observation_source": "synthetic_contract_probe", "hardware_verified": False,
                "message": "Native inference check passed with synthetic inputs. Check real observations while disarmed."}
    except Exception as exc:
        if operation == "check":
            _write(record.with_name("check.json"), {"state": "failed", "device": payload["device"],
                   "checked_at": time.time(), "error": str(exc), "inference": False})
        raise
    finally:
        if runner is not None:
            runner.close()
