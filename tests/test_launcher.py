"""The launcher really spawns a node as a subprocess and waits for /health."""
import os

from nerv.platform.launcher import Launcher
from nerv.platform.registry import Registry


def test_launch_calculator_tool_node(monkeypatch, tmp_path):
    monkeypatch.setenv("NERV_NODE_PORTS", "8180-8189")
    from nerv import config
    monkeypatch.setattr(config, "NODE_PORTS", "8180-8189")
    reg = Registry()
    lau = Launcher(reg)
    lau.log_dir = str(tmp_path)
    try:
        h = lau.ensure_tool(reg.tool("calculator"))
        assert h.alive() and h.meta.get("node") == "tool" and "calc" in h.meta.get("tools", [])
        assert lau.ensure_tool(reg.tool("calculator")) is h          # idempotent
        assert os.path.isfile(h.log_path)
    finally:
        lau.stop_all()
    assert not lau.nodes
