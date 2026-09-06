"""A body node with a fake family and no physics: exercises NERV/Body end to end."""
import io

from conftest import free_port, serve
from PIL import Image

from nerv.body.node import BodyNode, build_app
from nerv.body.skills import StopFlag
from nerv.nerve.body import KIND_PRIMITIVE, KIND_READ
from nerv.platform.client import RemoteBody


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeArm:
    name, family, version = "fake-arm", "arm", "t"

    def __init__(self):
        self.angle = 0.0
        self.calls = []
        self.held = False
        self.hold_reason = ""

    def tools(self):
        return [{"name": "read_angle", "kind": KIND_READ, "description": "read", "parameters": {"type": "object", "properties": {}}},
                {"name": "set_angle", "kind": KIND_PRIMITIVE, "description": "set",
                 "parameters": {"type": "object", "properties": {"deg": {"type": "number", "minimum": -90, "maximum": 90}}, "required": ["deg"]}}]

    def observe(self):
        return {"angle": self.angle}, [("camera:wrist", _png((255, 0, 0)))]

    def invoke(self, name, *, _progress=None, **args):
        self.calls.append((name, args))
        if name == "read_angle":
            return {"ok": True, "message": f"angle {self.angle}", "data": {"angle": self.angle}}
        if _progress:
            _progress(0.5, "halfway")
        self.angle = float(args["deg"])
        return {"ok": True, "message": f"set to {self.angle}", "data": {"angle": self.angle}}

    def status(self):
        return {"angle": self.angle}

    def stream_jpeg(self):
        return None

    def sensor_names(self):
        return ["camera:wrist"]

    def config_options(self):
        return []

    def set_option(self, key, value):
        return {"ok": False, "message": "no"}

    def hold(self, reason="operator"):
        self.held, self.hold_reason = True, reason
        return {"ok": True, "message": f"holding ({reason})", "held": True, "reason": reason}

    def release(self):
        self.held, self.hold_reason = False, ""
        return {"ok": True, "message": "released", "held": False, "reason": ""}

    def close(self):
        pass


def _start(armed):
    impl = FakeArm()
    node = BodyNode(impl, guidance="I am a fake arm.", stop=StopFlag(), armed=armed, world="bench", bus_kind="fake")
    port = free_port()
    srv, _ = serve(build_app(node, ["*"]), port)
    return impl, node, srv, f"http://127.0.0.1:{port}"


def test_handshake_observe_invoke_and_conformance():
    impl, node, srv, url = _start(armed=True)
    try:
        b = RemoteBody("fake-arm", url)
        caps = b.capabilities()
        assert caps.family == "arm" and caps.sensors == ["camera:wrist"]
        kinds = {t.name: t.kind for t in caps.tools}
        assert kinds == {"read_angle": "read", "set_angle": "primitive"}
        assert caps.guidance == "I am a fake arm."
        obs = b.perceive()
        assert obs.state["angle"] == 0.0 and obs.state["cameras"] == ["camera:wrist"] and len(obs.images) == 1
        got = []
        r = b.invoke("set_angle", _on_progress=lambda m, p, t: got.append((m, p)), deg=30)
        assert r.ok and r.data["angle"] == 30 and got and got[0][0] == "halfway"
        assert b.health()["armed"] is True
        from nerv.nerve.conformance import run
        rep = run(url, kind="body")
        assert rep.ok, rep.render()
    finally:
        srv.should_exit = True


def test_disarmed_node_refuses_mutation_but_serves_reads():
    impl, node, srv, url = _start(armed=False)
    try:
        b = RemoteBody("fake-arm", url)
        assert b.invoke("read_angle").ok
        r = b.invoke("set_angle", deg=10)
        assert not r.ok and "not armed" in r.message and impl.calls == [("read_angle", {})]
        assert b.set_config("armed", "true")["armed"] is True
        assert b.invoke("set_angle", deg=10).ok
    finally:
        srv.should_exit = True


def test_hold_is_an_emergency_stop_that_keeps_the_pose_not_a_power_cut():
    """/hold latches the pose and refuses every mutating verb until /release; reads still work,
    and the brain sees `held` in its observation."""
    impl, node, srv, url = _start(armed=True)
    try:
        b = RemoteBody("fake-arm", url)
        assert b.invoke("set_angle", deg=10).ok
        r = b.hold("operator")
        assert r["ok"] and r["held"] and impl.hold_reason == "operator"
        assert node.stop.is_set()                                   # a running skill would end now
        assert b.health()["held"] is True
        r = b.invoke("set_angle", deg=20)
        assert not r.ok and "holding" in r.message and impl.angle == 10   # nothing moved
        assert b.invoke("read_angle").ok                            # reads are never refused
        assert b.perceive().state["held"] is True                   # the brain is told
        assert b.release()["ok"] and b.health()["held"] is False
        assert b.invoke("set_angle", deg=20).ok and impl.angle == 20
    finally:
        srv.should_exit = True
