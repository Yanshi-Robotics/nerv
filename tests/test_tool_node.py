from conftest import free_port, serve

from nerv.platform.client import RemoteTool
from nerv.tool import calculator
from nerv.tool.node import build_tool_app


def test_calculator_node_over_mcp():
    port = free_port()
    app = build_tool_app("calculator", calculator.TOOLS, calculator.call)
    srv, _ = serve(app, port)
    try:
        c = RemoteTool("calculator", f"http://127.0.0.1:{port}")
        caps = c.capabilities()
        assert [t.name for t in caps.tools] == ["calc"] and caps.tools[0].kind == "read"
        r = c.invoke("calc", expression="17*23")
        assert r.ok and r.data["value"] == 391
        bad = c.invoke("calc", expression="1/0")
        assert not bad.ok and "zero" in bad.message
        from nerv.nerve.conformance import run
        rep = run(f"http://127.0.0.1:{port}", kind="tool")
        assert rep.ok, rep.render()
    finally:
        srv.should_exit = True
