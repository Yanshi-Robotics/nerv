"""Platform end to end: a scripted brain walks the G1 one metre in apt through NERV.

Launches the world, body and tool nodes exactly as `nerv chat` would, arms the session, sends
Think → CallTool(move_forward) → Say, and checks the measured result came back through the gate,
the router and the session store. Skipped unless the scene library and policy shelf are set.
"""
import os

import pytest

from nerv import config
from nerv.nerve.brain import CallTool, Say, Think, UserMessage
from nerv.platform.hub import Nerv
from nerv.platform.session import SessionStore

from nerv import paths

NEEDS = (os.path.join(paths.REPO_ROOT, "worlds", "build", "apt-g1.xml"),
         os.path.join(paths.REPO_ROOT, "policies", "g1-29dof-turn", "policy.onnx"))


class ScriptedBrain:
    name, model, vision = "scripted", "scripted", False

    def __init__(self):
        self.results = []

    def run_turn(self, link, user: UserMessage):
        obs = link.observe()
        assert obs is not None and "clearance_m" in obs.state and obs.images
        names = {t.name for t in link.tools()}
        assert {"move_forward", "turn_left", "turn_right", "calc"} <= names
        link.think(Think("walk", [CallTool("c1", "move_forward", {"meters": 1.0})]))
        self.results.append(link.call_tool(CallTool("c1", "move_forward", {"meters": 1.0})))
        self.results.append(link.call_tool(CallTool("c2", "calc", {"expression": "17*23"})))
        link.say(Say("done"))


@pytest.mark.skipif(any(not os.path.isfile(k) for k in NEEDS), reason="needs the worlds and policies submodules")
def test_scripted_brain_walks_one_metre(tmp_path, monkeypatch):
    monkeypatch.setenv("NERV_TRUST_ALL", "1")
    monkeypatch.setattr(config, "NODE_PORTS", "8180-8189")
    hub = Nerv(store=SessionStore(str(tmp_path / "s")))
    brain = ScriptedBrain()
    hub._brains["scripted"] = brain
    try:
        s = hub.new_session("scripted", "humanoid-unitree-g1", "apt")
        sid = s["id"]
        # disarmed: the gate refuses the skill and the brain is told why
        out = hub.handle(sid, "walk")
        r = brain.results[0]
        assert not r.ok and "not armed" in r.message and out["reply"] == "done"
        # armed: the skill really runs and comes back measured
        brain.results.clear()
        assert hub.arm(sid, True)["armed"] is True
        hub.handle(sid, "walk")
        r, calc = brain.results
        assert r.ok, r.message
        assert r.data.get("moved_m", 0) > 0.5 and not r.data.get("fallen"), r.data
        assert calc.ok and calc.data["value"] == 391
        print(f"[e2e] move_forward(1.0) via NERV: moved {r.data['moved_m']:.3f} m, reason={r.data.get('reason')}")
        msgs = hub.store.get(sid).messages
        assert [m["role"] for m in msgs].count("perception") == 2
    finally:
        hub.shutdown()
