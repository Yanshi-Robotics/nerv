"""A whole turn through the platform with the mock brain, an in-process body and a tool node."""
from conftest import free_port, serve

from nerv.brain.plugin import load as load_brain
from nerv.body.node import BodyNode, build_app
from nerv.body.skills import StopFlag
from nerv.platform import interrupt
from nerv.platform.client import RemoteBody, RemoteTool
from nerv.platform.gate import SafetyGate
from nerv.platform.router import Turn
from nerv.platform.session import SessionStore
from nerv.tool import calculator
from nerv.tool.node import build_tool_app
from test_body_node import FakeArm


def _setup(tmp_path, armed_session):
    impl = FakeArm()
    node = BodyNode(impl, guidance="fake", stop=StopFlag(), armed=True)
    bp, tp = free_port(), free_port()
    bs, _ = serve(build_app(node, ["*"]), bp)
    ts, _ = serve(build_tool_app("calculator", calculator.TOOLS, calculator.call), tp)
    store = SessionStore(str(tmp_path / "sessions"))
    s, _ = store.new("mock", "fake-arm", "bench", tools=["calculator"])
    store.set_armed(s.id, armed_session)
    s = store.get(s.id)
    body = RemoteBody("fake-arm", f"http://127.0.0.1:{bp}")
    tool = RemoteTool("calculator", f"http://127.0.0.1:{tp}")
    return impl, store, s, body, tool, (bs, ts)


def _stop(servers):
    for srv in servers:
        srv.should_exit = True


def test_mock_turn_calls_a_read_tool_and_replies(tmp_path):
    impl, store, s, body, tool, servers = _setup(tmp_path, armed_session=False)
    try:
        events = []
        turn = Turn(store, s, load_brain("mock"), body, None, [tool], SafetyGate(), events.append, world_name="bench")
        out = turn.run("hello")
        kinds = [e["type"] for e in events]
        assert kinds[0] == "start" and kinds[-1] == "done"
        assert "perception" in kinds and "tool_call" in kinds and "tool_result" in kinds and "reply" in kinds
        assert out["reply"] and "mock" in out["reply"].lower()
        msgs = store.get(s.id).messages
        roles = [m["role"] for m in msgs]
        assert roles.count("user") == 1 and "perception" in roles and "tool" in roles
        # the mock only calls read tools; disarmed session never blocked it
        assert all(e.get("allowed", True) for e in events if e["type"] == "gate")
    finally:
        _stop(servers)


def test_gate_refuses_mutation_when_disarmed_then_allows_when_armed(tmp_path):
    from nerv.nerve.brain import CallTool
    impl, store, s, body, tool, servers = _setup(tmp_path, armed_session=False)
    try:
        events = []
        turn = Turn(store, s, load_brain("mock"), body, None, [tool], SafetyGate(), events.append)
        r = turn.call_tool(CallTool("c1", "set_angle", {"deg": 20}))
        assert not r.ok and "not armed" in r.message and impl.calls == []
        r2 = turn.call_tool(CallTool("c2", "calc", {"expression": "17*23"}))
        assert r2.ok and r2.data["value"] == 391            # tools are not gated by arming
        store.set_armed(s.id, True)
        turn.session = store.get(s.id)
        r3 = turn.call_tool(CallTool("c3", "set_angle", {"deg": 20}))
        assert r3.ok and impl.angle == 20
        r4 = turn.call_tool(CallTool("c4", "set_angle", {"deg": 500}))
        assert not r4.ok and "maximum" in r4.message
    finally:
        _stop(servers)


def test_interrupt_and_step_ceiling_are_resumable_pauses(tmp_path):
    impl, store, s, body, tool, servers = _setup(tmp_path, armed_session=True)
    try:
        events = []
        turn = Turn(store, s, load_brain("mock"), body, None, [], SafetyGate(), events.append, max_steps=1)
        turn.observe()
        assert turn.stop_reason() == "steps"
        assert events[-1]["type"] == "reply" and events[-1]["stop_reason"] == "steps"
        events2 = []
        turn2 = Turn(store, s, load_brain("mock"), body, None, [], SafetyGate(), events2.append)
        interrupt.request(s.id)
        assert turn2.stop_reason() == "interrupt" and not interrupt.is_set(s.id)
    finally:
        _stop(servers)


def test_teleop_uses_the_same_gate_and_records_the_step(tmp_path):
    """The operator's remote control goes through the gate and lands in the session history."""
    from nerv.platform.hub import Nerv
    from nerv.platform.launcher import Launcher
    from nerv.platform.registry import Registry
    impl, store, s, body, tool, servers = _setup(tmp_path, armed_session=False)
    try:
        hub = Nerv(registry=Registry(), store=store, launcher=Launcher(Registry()))
        hub._bodies["fake-arm"] = body
        hub.launcher.nodes["body:fake-arm"] = type("H", (), {"url": body.base, "alive": lambda self: True,
                                                             "kind": "body", "name": "body:fake-arm",
                                                             "bus_url": "", "attached": True, "log_path": "",
                                                             "python": "", "meta": {}})()
        hub._tools["calculator"] = tool
        hub.launcher.nodes["tool:calculator"] = type("H", (), {"url": tool.base, "alive": lambda self: True,
                                                               "kind": "tool", "name": "tool:calculator",
                                                               "bus_url": "", "attached": True, "log_path": "",
                                                               "python": "", "meta": {}})()
        sheet = hub.tool_sheet(s.id)
        assert {t["name"] for t in sheet} >= {"set_angle", "read_angle", "calc"}
        assert next(t for t in sheet if t["name"] == "set_angle")["origin"] == "body"
        evs = list(hub.teleop_stream(s.id, "set_angle", {"deg": 15}))
        res = next(e for e in evs if e["type"] == "tool_result")
        assert not res["ok"] and "not armed" in res["message"] and impl.calls == []
        hub.arm(s.id, True)
        evs = list(hub.teleop_stream(s.id, "set_angle", {"deg": 15}))
        res = next(e for e in evs if e["type"] == "tool_result")
        assert res["ok"] and impl.angle == 15
        roles = [m["role"] for m in store.get(s.id).messages]
        assert roles[-2:] == ["assistant", "tool"]        # the operator's step is in the history
        assert store.get(s.id).messages[-2]["brain"] == "operator"
    finally:
        _stop(servers)
