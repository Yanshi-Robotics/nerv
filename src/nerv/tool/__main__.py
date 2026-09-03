"""Run a tool node:  python -m nerv.tool --tool calculator --port P"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

import uvicorn

from .node import build_tool_app

BUILTIN = {"calculator": "nerv.tool.calculator"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nerv tool node")
    ap.add_argument("--tool", required=True, help="tool name (registry) or python module")
    ap.add_argument("--host", default=os.environ.get("NERV_NODE_BIND_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args(argv)
    modname = BUILTIN.get(a.tool, a.tool)
    mod = importlib.import_module(modname)
    app = build_tool_app(a.tool, mod.TOOLS, mod.call, version=getattr(mod, "VERSION", "0"))
    print(f"[nerv tool] {a.tool} http://{a.host}:{a.port}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
