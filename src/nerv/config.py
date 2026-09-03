"""Central configuration — the single source for every tunable, default and model id.

pydantic-settings: defaults live here as fields, each bound to an environment variable
(``NERV_*``), overridable from the shell or the repository ``.env``. Nodes are separate
processes and never import this module; each node reads its own ``NERV_<NODE>_*`` knobs.
"""
from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import paths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=paths.ENV_FILE, env_file_encoding="utf-8",
                                      extra="ignore")

    # ---- turn loop ----
    max_steps: int = Field(60, validation_alias="NERV_MAX_STEPS", ge=1,
                           description="Steps per turn; a seatbelt, not a metronome.")
    turn_time_budget_s: float = Field(900, validation_alias="NERV_TURN_TIME_BUDGET_S", gt=0,
                                      description="Wall clock per turn (seconds).")
    context_token_budget: int = Field(6000, validation_alias="NERV_CONTEXT_BUDGET", ge=100,
                                      description="Sliding-window budget for history.")
    notes_max: int = Field(20, validation_alias="NERV_NOTES_MAX", ge=1)
    note_max_chars: int = Field(120, validation_alias="NERV_NOTE_MAX_CHARS", ge=10)

    # ---- text a node wrote, before it reaches the brain ----
    guidance_max_chars: int = Field(4000, validation_alias="NERV_GUIDANCE_MAX_CHARS", ge=100)
    tool_desc_max_chars: int = Field(1000, validation_alias="NERV_TOOL_DESC_MAX_CHARS", ge=50)

    # ---- node clients ----
    node_timeout: float = Field(30, validation_alias="NERV_NODE_TIMEOUT", gt=0,
                                description="Deadline for fast reads (capabilities, observe).")
    node_probe_timeout: float = Field(1.5, validation_alias="NERV_NODE_PROBE_TIMEOUT", gt=0)
    node_status_timeout: float = Field(5, validation_alias="NERV_NODE_STATUS_TIMEOUT", gt=0)
    node_connect_timeout: float = Field(5, validation_alias="NERV_NODE_CONNECT_TIMEOUT", gt=0)
    node_liveness_timeout: float = Field(20, validation_alias="NERV_NODE_LIVENESS_TIMEOUT", gt=0,
                                         description="No sign of life for this long = lost.")
    node_invoke_hard_cap: float = Field(180, validation_alias="NERV_NODE_INVOKE_HARD_CAP", gt=0,
                                        description="Overall cap on one action, progress or not.")
    bridge_watchdog_poll_s: float = Field(0.25, validation_alias="NERV_BRIDGE_WATCHDOG_POLL_S", gt=0)
    bridge_grace_s: float = Field(5, validation_alias="NERV_BRIDGE_GRACE_S", ge=0)
    tool_timeout: float = Field(15, validation_alias="NERV_TOOL_TIMEOUT", gt=0,
                                description="A tool node computes; it should answer in seconds.")

    # ---- launcher ----
    node_ports: str = Field("8112-8131", validation_alias="NERV_NODE_PORTS",
                            description="Port pool for launched nodes, 'lo-hi'.")
    node_health_wait_s: float = Field(60, validation_alias="NERV_NODE_HEALTH_WAIT_S", gt=0,
                                      description="How long to wait for a launched node's /health.")
    lerobot_python: str = Field("", validation_alias="NERV_LEROBOT_PYTHON",
                                description="Interpreter with LeRobot installed (real arms only; "
                                            "empty = this one).")
    node_bind_host: str = Field("127.0.0.1", validation_alias="NERV_NODE_BIND_HOST",
                                description="Launched nodes bind here. Loopback only by default.")

    # ---- sessions / logs ----
    title_max_len: int = Field(24, validation_alias="NERV_TITLE_MAX_LEN", ge=1)
    log_max_system: int = Field(8000, validation_alias="NERV_LOG_MAX_SYSTEM", ge=1)
    log_max_user: int = Field(8000, validation_alias="NERV_LOG_MAX_USER", ge=1)
    log_max_reply: int = Field(20000, validation_alias="NERV_LOG_MAX_REPLY", ge=1)
    signal_log_maxlen: int = Field(400, validation_alias="NERV_SIGNAL_LOG_MAXLEN", ge=1,
                                   description="In-memory ring of recent NERV signals for the UI.")
    signal_poll_interval_s: float = Field(0.25, validation_alias="NERV_SIGNAL_POLL_INTERVAL_S", gt=0)

    # ---- brains ----
    max_tokens: int = Field(1024, validation_alias="NERV_MAX_TOKENS", ge=1)
    ollama_probe_timeout: float = Field(0.6, validation_alias="NERV_OLLAMA_PROBE_TIMEOUT", gt=0)
    ollama_base_url: str = Field("http://localhost:11434/v1", validation_alias="OLLAMA_BASE_URL")
    default_brain: str = Field("claude", validation_alias="NERV_DEFAULT_BRAIN")
    model_claude: str = Field("claude-opus-4-8", validation_alias="NERV_CLAUDE_MODEL")
    model_claude_fast: str = Field("claude-haiku-4-5", validation_alias="NERV_CLAUDE_FAST_MODEL")
    model_openai: str = Field("gpt-5.5", validation_alias="NERV_OPENAI_MODEL")
    model_openai_mini: str = Field("gpt-5.4-mini", validation_alias="NERV_OPENAI_MINI_MODEL")
    model_ollama: str = Field("qwen3-vl:8b", validation_alias="NERV_OLLAMA_MODEL")

    # ---- HTTP ----
    cors_origins: str = Field("http://localhost:8100", validation_alias="NERV_CORS_ORIGINS")
    serve_host: str = Field("127.0.0.1", validation_alias="NERV_SERVE_HOST")
    serve_port: int = Field(8000, validation_alias="NERV_SERVE_PORT")


_settings = Settings()

MAX_STEPS = _settings.max_steps
TURN_TIME_BUDGET_S = _settings.turn_time_budget_s
CONTEXT_TOKEN_BUDGET = _settings.context_token_budget
NOTES_MAX = _settings.notes_max
NOTE_MAX_CHARS = _settings.note_max_chars
GUIDANCE_MAX_CHARS = _settings.guidance_max_chars
TOOL_DESC_MAX_CHARS = _settings.tool_desc_max_chars
NODE_TIMEOUT = _settings.node_timeout
NODE_PROBE_TIMEOUT = _settings.node_probe_timeout
NODE_STATUS_TIMEOUT = _settings.node_status_timeout
NODE_CONNECT_TIMEOUT = _settings.node_connect_timeout
NODE_LIVENESS_TIMEOUT = _settings.node_liveness_timeout
NODE_INVOKE_HARD_CAP = _settings.node_invoke_hard_cap
BRIDGE_WATCHDOG_POLL_S = _settings.bridge_watchdog_poll_s
BRIDGE_GRACE_S = _settings.bridge_grace_s
TOOL_TIMEOUT = _settings.tool_timeout
NODE_PORTS = _settings.node_ports
NODE_HEALTH_WAIT_S = _settings.node_health_wait_s
LEROBOT_PYTHON = _settings.lerobot_python
NODE_BIND_HOST = _settings.node_bind_host
TITLE_MAX_LEN = _settings.title_max_len
LOG_MAX_SYSTEM = _settings.log_max_system
LOG_MAX_USER = _settings.log_max_user
LOG_MAX_REPLY = _settings.log_max_reply
SIGNAL_LOG_MAXLEN = _settings.signal_log_maxlen
SIGNAL_POLL_INTERVAL_S = _settings.signal_poll_interval_s
MAX_TOKENS = _settings.max_tokens
OLLAMA_PROBE_TIMEOUT = _settings.ollama_probe_timeout
OLLAMA_BASE_URL = _settings.ollama_base_url
DEFAULT_BRAIN = _settings.default_brain
MODEL_CLAUDE = _settings.model_claude
MODEL_CLAUDE_FAST = _settings.model_claude_fast
MODEL_OPENAI = _settings.model_openai
MODEL_OPENAI_MINI = _settings.model_openai_mini
MODEL_OLLAMA = _settings.model_ollama
CORS_ORIGINS = [o.strip() for o in _settings.cors_origins.split(",") if o.strip()]
SERVE_HOST = _settings.serve_host
SERVE_PORT = _settings.serve_port

_RUNTIME_PARAMS_SHOWN = ("max_steps", "turn_time_budget_s", "context_token_budget", "notes_max")
_RUNTIME_PARAM_LABELS = {"max_steps": "Steps per turn", "turn_time_budget_s": "Time per turn (s)",
                         "context_token_budget": "Context budget (tokens)",
                         "notes_max": "Notebook capacity"}
_RUNTIME_PARAM_HINTS = {
    "max_steps": "A seatbelt, not a metronome: the brain ends a turn by answering in words.",
    "turn_time_budget_s": "Wall clock per turn — the real cost gate; reaching it is a resumable pause.",
    "context_token_budget": "History window, packed from the most recent message backwards.",
    "notes_max": "Notebook capacity; when full the brain is told and can strike an entry.",
}


def runtime_params() -> list[dict]:
    out = []
    for name in _RUNTIME_PARAMS_SHOWN:
        field = Settings.model_fields[name]
        alias = field.validation_alias
        out.append({"key": name, "label": _RUNTIME_PARAM_LABELS[name],
                    "value": getattr(_settings, name),
                    "env": alias if isinstance(alias, str) else name.upper(),
                    "description": _RUNTIME_PARAM_HINTS[name]})
    return out


def env_expand(value: str) -> str:
    """Expand ``${VAR}`` / ``$VAR`` in a registry string from the process environment."""
    return os.path.expandvars(value or "")


def port_pool() -> tuple[int, int]:
    lo, _, hi = NODE_PORTS.partition("-")
    lo_i, hi_i = int(lo), int(hi or lo)
    if hi_i < lo_i:
        raise ValueError(f"NERV_NODE_PORTS must be lo-hi, got {NODE_PORTS!r}")
    return lo_i, hi_i
