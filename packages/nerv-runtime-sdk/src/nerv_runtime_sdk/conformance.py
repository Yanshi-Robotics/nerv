"""Read-only protocol checks: these do not prepare devices, arm, or load policies."""
from __future__ import annotations

import json
import urllib.request

from .worker import providers


def check(provider_id: str, url: str | None = None):
    provider = providers()[provider_id].load()()
    manifest = provider.describe()
    manifest.check_protocol()
    json.dumps(manifest.model_dump())
    checks = {"protocol": "passed", "description_serializable": "passed",
              "preparation": "not_requested", "actuators": "not_requested"}
    if url:
        for resource, required in (("health", {"epoch", "family", "armed"}),
                                   ("sensors", {"sensors"}), ("config", {"options"}),
                                   ("policies", {"policies"})):
            with urllib.request.urlopen(url.rstrip("/") + "/" + resource, timeout=5) as response:
                data = json.load(response)
            if not required <= set(data):
                raise ValueError(f"runtime /{resource} is missing required fields")
            checks[resource] = "passed"
    return {"manifest": manifest.model_dump(), "checks": checks}
