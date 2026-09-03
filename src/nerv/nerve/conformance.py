"""Check a running node against its NERV interface, section by section.

body:  /health · MCP initialize · tools/list schemas · nerv://observation shape · guidance ·
       nerv://capabilities (kinds, family, sensors) · every tool kind is read|primitive|skill
tool:  /health · MCP initialize · tools/list schemas · no observation resource required
world: /health (epoch) · /sensors · /status answers · POST /reset answers
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx
from pydantic import AnyUrl

from .body import (CAPABILITIES_URI, GUIDANCE_PROMPT, KIND_PRIMITIVE, KIND_READ, KIND_SKILL,
                   OBSERVATION_URI)


@dataclass
class Check:
    section: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    url: str
    kind: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, section, name, ok, detail=""):
        self.checks.append(Check(section, name, bool(ok), detail))

    def render(self) -> str:
        lines = [f"NERV/{self.kind.capitalize()} conformance · {self.url}"]
        for c in self.checks:
            lines.append(f"  [{'ok' if c.ok else 'FAIL'}] {c.section:<14} {c.name}  {c.detail}")
        lines.append("PASS" if self.ok else "FAIL")
        return "\n".join(lines)


async def _mcp(url: str, rep: Report, kind: str) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as hc:
            async with streamable_http_client(url.rstrip("/") + "/mcp/", http_client=hc) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    rep.add("mcp", "initialize", True)
                    tl = await s.list_tools()
                    rep.add("tools", "tools/list", True, f"{len(tl.tools)} tools")
                    for t in tl.tools:
                        schema = t.inputSchema or {}
                        rep.add("tools", f"{t.name} schema object", schema.get("type") == "object")
                        rep.add("tools", f"{t.name} description", bool(t.description),
                                "" if t.description else "empty")
                    if kind == "body":
                        rl = await s.list_resources()
                        uris = {str(x.uri) for x in rl.resources}
                        rep.add("observation", "resource listed", OBSERVATION_URI in uris)
                        rd = await s.read_resource(AnyUrl(OBSERVATION_URI))
                        texts = [c for c in rd.contents if getattr(c, "text", None) is not None]
                        blobs = [c for c in rd.contents if getattr(c, "blob", None) is not None]
                        state = {}
                        try:
                            state = json.loads(texts[0].text) if texts else {}
                        except Exception:
                            pass
                        rep.add("observation", "state is JSON object", isinstance(state, dict))
                        cams = state.get("cameras") if isinstance(state, dict) else None
                        rep.add("observation", "cameras names match blobs",
                                (cams is None and not blobs) or (isinstance(cams, list) and len(cams) == len(blobs)),
                                f"{cams} vs {len(blobs)} blobs")
                        rep.add("capabilities", "resource listed", CAPABILITIES_URI in uris)
                        if CAPABILITIES_URI in uris:
                            cr = await s.read_resource(AnyUrl(CAPABILITIES_URI))
                            meta = json.loads(next(c.text for c in cr.contents if getattr(c, "text", None)))
                            rep.add("capabilities", "family declared", bool(meta.get("family")), str(meta.get("family")))
                            kinds = {n: v.get("kind") for n, v in (meta.get("tools") or {}).items()}
                            bad = [n for n, k in kinds.items() if k not in (KIND_READ, KIND_PRIMITIVE, KIND_SKILL)]
                            rep.add("capabilities", "tool kinds valid", not bad, str(bad) if bad else "")
                            rep.add("capabilities", "every tool has a kind", set(kinds) >= {t.name for t in tl.tools})
                        pl = await s.list_prompts()
                        rep.add("guidance", "prompt offered", any(p.name == GUIDANCE_PROMPT for p in pl.prompts))
    except Exception as e:
        rep.add("mcp", "connect", False, f"{type(e).__name__}: {e}")


def run(url: str, kind: str = "body") -> Report:
    rep = Report(url, kind)
    base = url.rstrip("/")
    try:
        r = httpx.get(base + "/health", timeout=5)
        h = r.json()
        rep.add("http", "/health 200", r.status_code == 200, str(h)[:120])
        if kind == "world":
            rep.add("http", "epoch present", bool(h.get("epoch")))
    except Exception as e:
        rep.add("http", "/health", False, str(e))
        return rep
    if kind == "world":
        for path, method in (("/sensors", "get"), ("/status", "get"), ("/reset", "post")):
            try:
                rr = getattr(httpx, method)(base + path, timeout=10)
                rep.add("http", f"{method.upper()} {path}", rr.status_code == 200)
            except Exception as e:
                rep.add("http", f"{method.upper()} {path}", False, str(e))
        return rep
    asyncio.run(_mcp(base, rep, kind))
    return rep
