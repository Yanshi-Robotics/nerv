"""The brain registry: name → provider. One table, read at call time so .env applies."""
from __future__ import annotations

import json
import os
import urllib.request

from ... import config
from .base import LLM
from .claude import ClaudeLLM
from .mock import MockLLM
from .openai_compat import OpenAICompatLLM


def _ollama_tags(base_url: str) -> set[str]:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models",
                                    timeout=config.OLLAMA_PROBE_TIMEOUT) as r:
            return {m.get("id", "") for m in json.load(r).get("data", [])}
    except Exception:
        return set()


def _registry() -> dict[str, dict]:
    okey = os.getenv("OPENAI_API_KEY", "")
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    ollama = config.OLLAMA_BASE_URL
    tags: set[str] | None = None

    def ollama_ready(model: str) -> bool:
        nonlocal tags
        if tags is None:
            tags = _ollama_tags(ollama)
        return model in tags

    return {
        "claude": {"vendor": "Anthropic", "label": "Claude (strong)", "model": config.MODEL_CLAUDE,
                   "hosting": "api", "build": lambda: ClaudeLLM(config.MODEL_CLAUDE),
                   "ready": lambda: has_anthropic},
        "claude-fast": {"vendor": "Anthropic", "label": "Claude (fast)", "model": config.MODEL_CLAUDE_FAST,
                        "hosting": "api", "build": lambda: ClaudeLLM(config.MODEL_CLAUDE_FAST),
                        "ready": lambda: has_anthropic},
        "openai": {"vendor": "OpenAI", "label": "OpenAI (strong)", "model": config.MODEL_OPENAI,
                   "hosting": "api", "build": lambda: OpenAICompatLLM(config.MODEL_OPENAI, None, okey),
                   "ready": lambda: bool(okey)},
        "openai-mini": {"vendor": "OpenAI", "label": "OpenAI (mini)", "model": config.MODEL_OPENAI_MINI,
                        "hosting": "api",
                        "build": lambda: OpenAICompatLLM(config.MODEL_OPENAI_MINI, None, okey),
                        "ready": lambda: bool(okey)},
        "ollama": {"vendor": "Ollama · local", "label": "Local (Ollama)", "model": config.MODEL_OLLAMA,
                   "hosting": "local",
                   "build": lambda: OpenAICompatLLM(config.MODEL_OLLAMA, ollama, "ollama"),
                   "ready": lambda: ollama_ready(config.MODEL_OLLAMA)},
        "mock": {"vendor": "built in · no key needed",
                 "label": "Mock (does not think — demonstrates the chain)", "model": "mock",
                 "hosting": "local", "build": MockLLM, "ready": lambda: True},
    }


def list_brains() -> list[dict]:
    return [{"name": n, "vendor": s["vendor"], "label": s["label"], "model": s["model"],
             "hosting": s["hosting"], "available": s["ready"]()} for n, s in _registry().items()]


def make_llm(name: str | None = None) -> LLM:
    name = name or config.DEFAULT_BRAIN
    reg = _registry()
    if name not in reg:
        raise KeyError(f"Unknown brain: {name}. Available: {', '.join(reg)}")
    return reg[name]["build"]()
