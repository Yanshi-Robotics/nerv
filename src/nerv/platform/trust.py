"""Which nodes the operator has reviewed and approved.

A body or tool node is a separate process reached over a URL, and its own text — guidance
into the system prompt, tool descriptions into the tool sheet — is written by whoever runs
that URL. So a node may describe itself, but only the operator may authorise it, and the
approval is bound to the SHA-256 of what was reviewed (SSH host-key style): if the node
comes back different, the operator is asked again and shown what changed.

This decides whether to connect at all. It does not solve prompt injection; the fence and
the length caps below raise the bar, and the human reading the manifest is the protection.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import paths

TRUST_ALL_ENV = "NERV_TRUST_ALL"
UNKNOWN, CHANGED, TRUSTED = "unknown", "changed", "trusted"
FENCE_OPEN = "<<<BEGIN_NODE_TEXT"
FENCE_CLOSE = ">>>END_NODE_TEXT"


def trust_all_enabled() -> bool:
    return os.environ.get(TRUST_ALL_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def manifest(url: str, tools, guidance: str) -> dict:
    return {"url": url, "guidance": guidance or "",
            "tools": sorted(({"name": t.name, "kind": t.kind, "description": t.description,
                              "parameters": t.parameters} for t in (tools or [])),
                            key=lambda d: d["name"])}


def manifest_hash(m: dict) -> str:
    blob = json.dumps(m, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def manifest_diff(old: dict, new: dict) -> list[str]:
    lines: list[str] = []
    if old.get("url") != new.get("url"):
        lines.append(f"URL: {old.get('url')!r} → {new.get('url')!r}")
    if (old.get("guidance") or "") != (new.get("guidance") or ""):
        lines.append(f"guidance changed ({len(old.get('guidance') or '')} chars → "
                     f"{len(new.get('guidance') or '')} chars)")
    ot = {t["name"]: t for t in old.get("tools", [])}
    nt = {t["name"]: t for t in new.get("tools", [])}
    for name in sorted(set(nt) - set(ot)):
        lines.append(f"tool added: {name} (kind={nt[name].get('kind')})")
    for name in sorted(set(ot) - set(nt)):
        lines.append(f"tool removed: {name}")
    for name in sorted(set(ot) & set(nt)):
        o, n = ot[name], nt[name]
        if o.get("kind") != n.get("kind"):
            lines.append(f"⚠ tool {name}: kind changed, {o.get('kind')} → {n.get('kind')}")
        if o.get("description") != n.get("description"):
            lines.append(f"tool {name}: description changed")
        if o.get("parameters") != n.get("parameters"):
            lines.append(f"tool {name}: parameter schema changed")
    return lines or ["something changed, but no specific field could be identified"]


def fence(text: str, max_chars: int) -> str:
    clean = (text or "").replace(FENCE_OPEN, "").replace(FENCE_CLOSE, "")
    if len(clean) > max_chars:
        clean = clean[:max_chars] + (f"\n… (This text exceeds {max_chars} characters and was "
                                     f"truncated. What you have read is not all of it.)")
    return f"{FENCE_OPEN}\n{clean}\n{FENCE_CLOSE}"


def clip(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[:max_chars] + "… (description too long; truncated)"


@dataclass
class TrustDecision:
    state: str
    manifest: dict
    approved_manifest: dict | None = None
    reason: str = ""
    changes: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.state == TRUSTED


class TrustStore:
    def __init__(self, path: str | None = None):
        home = os.environ.get("NERV_HOME") or os.path.dirname(paths.TRUST_FILE)
        self.path = path or os.path.join(home, os.path.basename(paths.TRUST_FILE))
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"version": 1, "nodes": {}}
        data.setdefault("nodes", {})
        return data

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def check(self, url: str, tools, guidance: str) -> TrustDecision:
        m = manifest(url, tools, guidance)
        if trust_all_enabled():
            return TrustDecision(TRUSTED, m, reason=f"{TRUST_ALL_ENV} is on — development only")
        rec = self._data["nodes"].get(url)
        if rec is None:
            return TrustDecision(UNKNOWN, m, reason="this node has not been approved yet")
        if rec.get("hash") == manifest_hash(m):
            return TrustDecision(TRUSTED, m, approved_manifest=rec.get("manifest"))
        old = rec.get("manifest") or {}
        return TrustDecision(CHANGED, m, approved_manifest=old,
                             reason="this node's manifest is not what you approved last time",
                             changes=manifest_diff(old, m))

    def approved_record(self, url: str) -> dict | None:
        return self._data["nodes"].get(url)

    def list_approved(self) -> dict:
        return dict(self._data["nodes"])

    def approve(self, url: str, tools, guidance: str, label: str = "") -> str:
        m = manifest(url, tools, guidance)
        h = manifest_hash(m)
        self._data["nodes"][url] = {"hash": h, "label": label,
                                    "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "manifest": m}
        self._save()
        return h

    def revoke(self, url: str) -> bool:
        if self._data["nodes"].pop(url, None) is None:
            return False
        self._save()
        return True
