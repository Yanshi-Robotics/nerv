"""Clients for the nodes the platform talks to.

RemoteBody  — NERV/Body over MCP (tools, observation, guidance, config, capabilities) plus the
              control plane over HTTP (/health, /status, /config, /stream).
RemoteTool  — NERV/Tool over MCP (tools only), short deadline.
WorldControl — NERV/World control plane over HTTP (health, epoch, status, ambient sensors).
Every call is recorded in the session log; capabilities are fetched once and cached; a node's
text reaches the brain only after the operator approved its manifest.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Optional

import httpx
from pydantic import AnyUrl

from .. import config
from ..nerve.body import (CAPABILITIES_URI, CONFIG_URI, GUIDANCE_PROMPT, KIND_PRIMITIVE,
                          KIND_READ, OBSERVATION_URI, ActionResult, Capabilities, Observation,
                          ToolSpec)
from ..nerve import world as W
from . import mcp_bridge, trust
from .mcp_bridge import run_sync, with_session
from .session import log


class _McpNode:
    """Shared handshake/trust/invoke machinery for body and tool nodes."""
    log_kind = "body_call"
    what = "node"

    def __init__(self, name: str, base_url: str, timeout: float = config.NODE_TIMEOUT) -> None:
        self.name = name
        self.base = base_url.rstrip("/")
        self.mcp_url = self.base + "/mcp/"
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)
        self._caps: Capabilities | None = None
        self._raw: Capabilities | None = None
        self._trust: trust.TrustDecision | None = None

    # -- trust ----------------------------------------------------------------------------
    def trust_decision(self) -> trust.TrustDecision:
        self.capabilities()
        return self._trust

    def raw_capabilities(self) -> Capabilities:
        self.capabilities()
        return self._raw

    def approve(self, store: trust.TrustStore | None = None) -> str:
        raw = self.raw_capabilities()
        h = (store or trust.TrustStore()).approve(self.base, raw.tools, raw.guidance, self.name)
        self.refresh()
        return h

    def refresh(self) -> None:
        self._caps = self._raw = self._trust = None

    # -- capabilities ---------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        if self._caps is not None:
            return self._caps

        async def op(s):
            tl = await s.list_tools()
            kinds: dict = {}
            family, sensors, version = "", [], ""
            cfg: dict = {}
            try:
                rl = await s.list_resources()
                uris = {str(r.uri) for r in rl.resources}
                if CAPABILITIES_URI in uris:
                    rd = await s.read_resource(AnyUrl(CAPABILITIES_URI))
                    for c in rd.contents:
                        if getattr(c, "text", None):
                            meta = json.loads(c.text) or {}
                            kinds = meta.get("tools") or {}
                            family = meta.get("family", "")
                            sensors = list(meta.get("sensors") or [])
                            version = str(meta.get("version", ""))
                            break
                if CONFIG_URI in uris:
                    rd = await s.read_resource(AnyUrl(CONFIG_URI))
                    for c in rd.contents:
                        if getattr(c, "text", None):
                            cfg = json.loads(c.text) or {}
                            break
            except Exception:
                pass
            tools = []
            for t in tl.tools:
                ro = bool(t.annotations and t.annotations.readOnlyHint)
                kind = (kinds.get(t.name) or {}).get("kind") or (KIND_READ if ro else KIND_PRIMITIVE)
                tools.append(ToolSpec(name=t.name, description=t.description or "",
                                      parameters=t.inputSchema or {"type": "object", "properties": {}},
                                      kind=kind))
            guidance = ""
            try:
                pl = await s.list_prompts()
                if any(p.name == GUIDANCE_PROMPT for p in pl.prompts):
                    gp = await s.get_prompt(GUIDANCE_PROMPT, {})
                    guidance = "".join(m.content.text for m in gp.messages
                                       if getattr(m.content, "text", None))
            except Exception:
                pass
            return tools, guidance, cfg, family, sensors, version

        t0 = time.perf_counter()
        tools, guidance, cfg, family, sensors, version = run_sync(
            with_session(self.mcp_url, op, self.timeout), self.timeout + config.BRIDGE_GRACE_S)
        self._raw = Capabilities(name=self.name, version=version, tools=tools, guidance=guidance,
                                 config=cfg, family=family, sensors=sensors)
        self._trust = trust.TrustStore().check(self.base, tools, guidance)
        if self._trust.allowed:
            self._caps = self._raw
        else:
            self._caps = Capabilities(name=self.name, version=version, tools=[], guidance="",
                                      config=cfg, family=family, sensors=sensors)
        log.record_node(self.log_kind, self.name, "capabilities", "handshake [mcp]",
                        (time.perf_counter() - t0) * 1000,
                        {"n_tools": len(tools), "tools": [t.name for t in tools],
                         "trusted": self._trust.allowed, "family": family})
        return self._caps

    # -- invoke ---------------------------------------------------------------------------
    def invoke(self, name: str, *, _on_progress: Optional[Callable] = None,
               _should_abort: Optional[Callable[[], bool]] = None, **kwargs: Any) -> ActionResult:
        if self._caps is None:
            self.capabilities()
        t0 = time.perf_counter()
        beat = mcp_bridge.Beat()

        async def _cb(progress: float, total, message) -> None:
            beat.touch()
            log.record_node(self.log_kind, self.name, "progress", f"{name}: {message or ''}",
                            (time.perf_counter() - t0) * 1000, {"progress": progress})
            if _on_progress is not None:
                try:
                    _on_progress(message or "", progress, total)
                except Exception:
                    pass

        async def op(s):
            r = await s.call_tool(name, kwargs, progress_callback=_cb)
            text = "".join(c.text for c in r.content if getattr(c, "text", None))
            return (not bool(getattr(r, "isError", False))), text, (r.structuredContent or {})

        try:
            ok, text, data = mcp_bridge.run_alive(
                with_session(self.mcp_url, op, config.NODE_CONNECT_TIMEOUT,
                             read_timeout=config.NODE_LIVENESS_TIMEOUT + config.BRIDGE_GRACE_S),
                beat=beat, liveness_s=config.NODE_LIVENESS_TIMEOUT,
                hard_cap_s=config.NODE_INVOKE_HARD_CAP, should_abort=_should_abort)
            res = ActionResult(ok=ok, message=text, data=data)
        except mcp_bridge.LivenessTimeout:
            res = ActionResult(False, f"Lost contact with the {self.what}: no sign of life for "
                                      f"{config.NODE_LIVENESS_TIMEOUT:g}s")
        except mcp_bridge.HardCapTimeout:
            res = ActionResult(False, f"The action exceeded its overall cap of "
                                      f"{config.NODE_INVOKE_HARD_CAP:g}s; gave up waiting")
        except mcp_bridge.CallAborted:
            res = ActionResult(False, "Stopped by the operator while the action was running")
        except Exception as e:
            res = ActionResult(False, f"(The call to the {self.what} failed: {type(e).__name__}: {e})")
        log.record_node(self.log_kind, self.name, "invoke", f"{name}({kwargs})",
                        (time.perf_counter() - t0) * 1000,
                        {"ok": res.ok, "message": res.message[:500], "has_data": bool(res.data)})
        return res

    # -- control plane ----------------------------------------------------------------------
    def online(self) -> bool:
        try:
            self._http.get(self.base + "/health", timeout=config.NODE_PROBE_TIMEOUT)
            return True
        except Exception:
            return False

    def health(self) -> dict | None:
        try:
            r = self._http.get(self.base + "/health", timeout=config.NODE_PROBE_TIMEOUT)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def status(self) -> dict | None:
        try:
            r = self._http.get(self.base + "/status", timeout=config.NODE_STATUS_TIMEOUT)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def set_config(self, key: str, value) -> dict:
        try:
            r = self._http.post(self.base + "/config", json={"key": key, "value": value},
                                timeout=config.NODE_STATUS_TIMEOUT)
            return r.json()
        except Exception as e:
            return {"ok": False, "message": f"The {self.what} `{self.name}` did not answer: {e}"}

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass


class RemoteBody(_McpNode):
    log_kind = "body_call"
    what = "body"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._last_state: dict | None = None

    def perceive(self) -> Observation:
        async def op(s):
            rd = await s.read_resource(AnyUrl(OBSERVATION_URI))
            state: dict = {}
            blobs: list[bytes] = []
            for c in rd.contents:
                if getattr(c, "text", None) is not None:
                    try:
                        state = json.loads(c.text) or {}
                    except Exception:
                        state = {}
                elif getattr(c, "blob", None) is not None:
                    blobs.append(base64.b64decode(c.blob))
            return state, blobs

        t0 = time.perf_counter()
        try:
            state, blobs = run_sync(with_session(self.mcp_url, op, self.timeout),
                                    self.timeout + config.BRIDGE_GRACE_S)
        except Exception as e:
            state, blobs = {"error": f"observation unavailable: {type(e).__name__}"}, []
        names = state.get("cameras") if isinstance(state.get("cameras"), list) else []
        images = [{"name": (names[i] if i < len(names) else ""), "png": b} for i, b in enumerate(blobs)]
        obs = Observation(state=state, images=images)
        log.record_node(self.log_kind, self.name, "perceive", "perceive()",
                        (time.perf_counter() - t0) * 1000,
                        {"n_images": len(blobs), "img_bytes": sum(len(b) for b in blobs),
                         "state": state})
        self._last_state = state
        return obs

    def last_state(self) -> dict | None:
        return self._last_state


class RemoteTool(_McpNode):
    log_kind = "tool_call"
    what = "tool"

    def __init__(self, name: str, base_url: str) -> None:
        super().__init__(name, base_url, timeout=config.TOOL_TIMEOUT)


class WorldControl:
    """The world node's control plane. The brain never sees any of this."""

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.base = base_url.rstrip("/")
        self._http = httpx.Client(timeout=config.NODE_STATUS_TIMEOUT)

    def health(self) -> dict | None:
        try:
            r = self._http.get(self.base + W.HEALTH, timeout=config.NODE_PROBE_TIMEOUT)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def online(self) -> bool:
        return self.health() is not None

    def epoch(self) -> str:
        h = self.health() or {}
        return str(h.get("epoch", ""))

    def status(self) -> dict | None:
        try:
            r = self._http.get(self.base + W.STATUS)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def sensors(self) -> list[str]:
        try:
            r = self._http.get(self.base + W.SENSORS)
            return list(r.json().get("sensors", [])) if r.status_code == 200 else []
        except Exception:
            return []

    def sensor(self, name: str) -> tuple[bytes, str] | None:
        """One frame of an ambient sensor stream: (data, mime). Sensors only — never truth."""
        t0 = time.perf_counter()
        try:
            r = self._http.get(self.base + W.SENSORS + "/" + name)
            if r.status_code != 200:
                return None
            log.record_node("world_call", self.name, "sensor", name, (time.perf_counter() - t0) * 1000,
                            {"bytes": len(r.content), "mime": r.headers.get("content-type", "")})
            return r.content, r.headers.get("content-type", "application/octet-stream")
        except Exception:
            return None

    def reset(self) -> dict:
        try:
            return self._http.post(self.base + W.RESET).json()
        except Exception as e:
            return {"ok": False, "message": str(e)}
