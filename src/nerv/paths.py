"""Where things live on disk — the single place that knows.

Runs from a source checkout (``pyproject.toml`` next to the package) or from an installed
wheel; in the first case data lands in the repository, in the second under ``~/.nerv``.
Nothing here imports the rest of ``nerv``.
"""
from __future__ import annotations

import os

_PKG = os.path.dirname(os.path.abspath(__file__))          # .../src/nerv
_SRC = os.path.dirname(_PKG)                               # .../src
REPO_ROOT = os.path.dirname(_SRC)                          # repository root in a checkout

NERV_HOME = os.environ.get("NERV_HOME") or os.path.join(os.path.expanduser("~"), ".nerv")
TRUST_FILE = os.path.join(NERV_HOME, "trust.json")

_IN_CHECKOUT = os.path.isfile(os.path.join(REPO_ROOT, "pyproject.toml"))
DATA_ROOT = REPO_ROOT if _IN_CHECKOUT else NERV_HOME

CACHE_DIR = os.path.join(DATA_ROOT, ".cache")
LOGS_DIR = os.path.join(DATA_ROOT, "logs")
MEMORY_DIR = os.path.join(DATA_ROOT, "memory")
SESSIONS_DIR = os.path.join(MEMORY_DIR, "sessions")
ENV_FILE = os.path.join(DATA_ROOT, ".env")

# The registry directories ship with the repository (bodies/, worlds/, tools/). Extra roots
# can be added with NERV_REGISTRY_PATHS (colon-separated) for out-of-tree nodes.
REGISTRY_ROOT = REPO_ROOT if _IN_CHECKOUT else NERV_HOME
BODIES_DIR = os.path.join(REGISTRY_ROOT, "bodies")
WORLDS_DIR = os.path.join(REGISTRY_ROOT, "worlds")
TOOLS_DIR = os.path.join(REGISTRY_ROOT, "tools")
