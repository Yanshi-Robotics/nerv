"""VLM catalogue, capability checks and write-only credentials with explicit precedence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

_lock = threading.RLock()
CHECKS = ("image", "tools", "continuation")


def private_write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


class CredentialStore:
    def __init__(self, root: Path):
        self.path = root / "credentials.json"

    def _read(self):
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
            raise PermissionError("credential storage must be a private regular file (0600)")
        return json.loads(self.path.read_text())

    def resolve(self, reference: str):
        return os.environ.get(reference) or self._read().get(reference, "")

    def describe(self, reference: str):
        environment = bool(os.environ.get(reference))
        configured = environment or bool(self._read().get(reference))
        return {"configured": configured, "source": "environment" if environment else "store" if configured else None,
                "writable": not environment}

    def set(self, reference: str, value: str | None):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", reference):
            raise ValueError("invalid credential reference")
        if os.environ.get(reference):
            raise ValueError("launch environment owns this credential; remove its override before editing")
        if value is not None and (not value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError("enter a nonempty API key without surrounding whitespace")
        with _lock:
            values = self._read()
            if value is None:
                values.pop(reference, None)
            else:
                values[reference] = value
            private_write(self.path, values)


class VisualModel(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    provider: str
    label: str
    model: str
    base_url: str
    protocol: Literal["responses", "deepseek_responses"] = "responses"
    credential_ref: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    input_modalities: list[str]
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_continuation: bool = True
    provider_output_limit: int | None = Field(None, gt=0)
    provider_default_effort: str | None = None
    limits_basis: str = ""
    efforts: list[str] = Field(default_factory=list)
    evidence: str
    context_window: int | None = Field(None, gt=0)
    max_output_tokens: int = Field(4096, gt=0)
    request_timeout_s: float = Field(15, gt=0)
    enabled: bool = True

    @model_validator(mode="after")
    def visual(self):
        if "image" not in self.input_modalities or not self.evidence.strip():
            raise ValueError("only models with documented image input can enter the VLM registry")
        if not (self.base_url.startswith("https://") or self.base_url.startswith("http://127.0.0.1:")
                or self.base_url.startswith("http://localhost:")):
            raise ValueError("use HTTPS or an explicit loopback model endpoint")
        address = urlsplit(self.base_url)
        if address.username or address.password or address.query or address.fragment:
            raise ValueError("use a credential-free API base URL without query or fragment")
        if self.provider_output_limit and self.max_output_tokens > self.provider_output_limit:
            raise ValueError("configured output exceeds the documented provider limit")
        return self

    def signature(self):
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()


class ModelRegistry:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("NERV_HOME") or Path.home() / ".nerv")
        self.path = self.root / "vlm.json"
        self.credentials = CredentialStore(self.root)

    def _read(self):
        defaults = json.loads(Path(__file__).with_name("catalog.json").read_text())
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        data["models"] = {**{m["id"]: m for m in defaults}, **data.get("models", {})}
        data.setdefault("checks", {})
        data.setdefault("physical", {})
        data.setdefault("efforts", {})
        return data

    def model(self, model_id: str):
        data = self._read()
        if model_id not in data["models"]:
            raise KeyError("unknown registered VLM")
        return VisualModel(**data["models"][model_id])

    def request_signature(self, model):
        # Keys stay private. Changing credentials, endpoint, model or reasoning
        # settings must not inherit validation from a different request contract.
        effort = self._read()["efforts"].get(model.id)
        key = self.credentials.resolve(model.credential_ref)
        return hashlib.sha256(json.dumps([model.signature(), effort, key]).encode()).hexdigest()

    def list(self, binding_signature=None):
        data = self._read()
        result = []
        for raw in data["models"].values():
            model = VisualModel(**raw)
            credentials = self.credentials.describe(model.credential_ref)
            checks = data["checks"].get(model.id, {})
            valid = checks.get("signature") == self.request_signature(model) and all(checks.get(k) == "passed" for k in CHECKS)
            physical = data["physical"].get(model.id, {})
            if (physical.get("signature") != self.request_signature(model)
                    or not binding_signature or physical.get("binding_signature") != binding_signature):
                physical = {"status": "unverified", "record": physical}
            result.append({**model.model_dump(), "credential": credentials, "checks": checks,
                           "effort": data["efforts"].get(model.id), "physical": physical,
                           "available": bool(model.enabled and model.supports_tools and model.supports_streaming and model.supports_continuation and credentials["configured"] and valid)})
        return result

    def record_acceptance(self, model_id, signature, status, evidence, binding_signature):
        if not binding_signature or status not in ("pending", "failed", "passed") or not evidence.strip():
            raise ValueError("supply a physical status and retained operator evidence reference")
        with _lock:
            model = self.model(model_id)
            if signature != self.request_signature(model):
                raise ValueError("physical evidence does not match the current request signature")
            if status == "passed" and not any(m["id"] == model_id and m["available"] for m in self.list()):
                raise ValueError("matching API checks must pass before recording physical acceptance")
            data = self._read()
            data["physical"][model_id] = {"status": status, "signature": signature, "evidence": evidence,
                                          "recorded_at": time.time(), "source": "operator_record", "binding_signature": binding_signature}
            private_write(self.path, data)

    def upsert(self, raw: dict):
        model = VisualModel(**raw)
        with _lock:
            data = self._read()
            data["models"][model.id] = model.model_dump()
            data["checks"].pop(model.id, None)
            private_write(self.path, data)
        return model

    def selection(self):
        return self._read().get("selection", "")

    def remember(self, model_id):
        self.model(model_id)
        with _lock:
            data = self._read()
            data["selection"] = model_id
            private_write(self.path, data)

    def select(self, model_id: str, effort: str | None = None):
        model = self.model(model_id)
        if effort is not None and effort not in model.efforts:
            raise ValueError("this model does not support the selected reasoning effort")
        with _lock:
            data = self._read()
            data["selection"] = model_id
            data["efforts"][model_id] = effort
            private_write(self.path, data)

    def invalidate_credential(self, reference):
        with _lock:
            data = self._read()
            for model in self.list():
                if model["credential_ref"] == reference:
                    data["checks"].pop(model["id"], None)
            private_write(self.path, data)

    def provider(self, model_id: str):
        from ..brain.providers.responses import ResponsesLLM
        model = self.model(model_id)
        key = self.credentials.resolve(model.credential_ref)
        if not key:
            raise ValueError("configure this provider's API key first")
        effort = self._read()["efforts"].get(model_id)
        if effort is not None and effort not in model.efforts:
            raise ValueError("stored effort is no longer supported; select a valid setting")
        return ResponsesLLM(model, key, effort)

    def validate(self, model_id: str, provider=None):
        # Called only by the explicit operator validation endpoint. Discovery and
        # key saving never trigger paid requests or touch robot tools.
        import io
        import secrets
        from PIL import Image, ImageDraw
        from ..nerve.body import ToolSpec
        model = self.model(model_id)
        client = provider or self.provider(model_id)
        code = secrets.token_hex(3).upper()
        picture = Image.new("RGB", (360, 100), "white")
        ImageDraw.Draw(picture).text((20, 25), code, fill="black", font_size=40)
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        tool = ToolSpec("vlm_probe", "Return the exact code visible in the supplied test image.",
                        {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"],
                         "additionalProperties": False}, "read")
        checks = {key: "unknown" for key in CHECKS}
        checks["signature"] = self.request_signature(model)
        checks["checked_at"] = time.time()
        try:
            history = [{"role": "user", "text": "Read the code in the test image and call vlm_probe. This is an API test with no robot."}]
            first = client.chat("Use the supplied visual test tool, then acknowledge its result.", history,
                                [tool], [{"name": "validation", "png": buffer.getvalue()}])
            if first.finish_status != "completed":
                raise ValueError("visual test request did not complete")
            if len(first.tool_calls) != 1 or first.tool_calls[0].name != "vlm_probe":
                raise ValueError("tool call was not returned")
            checks["tools"] = "passed"
            call = first.tool_calls[0]
            if call.arguments.get("code") != code:
                raise ValueError("image code did not match")
            checks["image"] = "passed"
            history += [{"role": "assistant", "text": first.text or "", "tool_calls": first.tool_calls,
                         "continuation": first.continuation},
                        {"role": "tool", "id": call.id, "name": call.name, "content": "Visual test code matched."}]
            second = client.chat("Acknowledge the completed test. Do not call further tools.", history, [], None)
            if second.finish_status != "completed" or second.tool_calls or not second.text:
                raise ValueError("continuation did not finish")
            checks["continuation"] = "passed"
        except Exception as error:
            checks["error"] = type(error).__name__
        with _lock:
            data = self._read()
            if self.request_signature(self.model(model_id)) != checks["signature"]:
                raise ValueError("model changed during validation")
            data["checks"][model_id] = checks
            private_write(self.path, data)
        return checks
