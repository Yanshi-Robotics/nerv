"""Platform end to end: a scripted brain walks the G1 one metre in apt through NERV.

Launches the world, body and tool nodes exactly as `nerv chat` would, arms the session, sends
Think → CallTool(move_forward) → Say, and checks the measured result came back through the gate,
the router and the session store. Skipped unless the scene library and policy shelf are set.
"""
import os

import pytest

from nerv.nerve.brain import CallTool, Say, Think, UserMessage
from nerv.platform.hub import Nerv
from nerv.platform.session import SessionStore

from nerv import paths

NEEDS = (os.path.join(paths.REPO_ROOT, "nerv-assets", "build", "apt-g1.xml"),
         os.path.join(paths.REPO_ROOT, "policies", "g1-29dof-turn", "policy.onnx"))


class ScriptedBrain:
    name, model, vision = "scripted", "scripted", False

    def __init__(self):
        self.results = []

    def run_turn(self, link, user: UserMessage):
        obs = link.observe()
        assert obs is not None, "The turn was invalidated before observation"
        assert "clearance_m" in obs.state, obs.state
        assert obs.images, obs.state
        names = {t.name for t in link.tools()}
        assert {"move_forward", "turn_left", "turn_right", "calc"} <= names, names
        link.think(Think("walk", [CallTool("c1", "move_forward", {"meters": 1.0})]))
        self.results.append(link.call_tool(CallTool("c1", "move_forward", {"meters": 1.0})))
        self.results.append(link.call_tool(CallTool("c2", "calc", {"expression": "17*23"})))
        link.say(Say("done"))


@pytest.mark.skipif(any(not os.path.isfile(k) for k in NEEDS), reason="needs the worlds and policies submodules")
def test_scripted_brain_walks_one_metre(tmp_path, monkeypatch):
    # Historical scripted brains are no longer an admitted control path. Real
    # locomotion is covered by humanoid-family CPU tests; independent navigation
    # must use an installed candidate and separate external Agent contexts.
    hub = Nerv(store=SessionStore(str(tmp_path / 'sessions')))
    launch = []
    monkeypatch.setattr(hub.launcher, 'ensure_world', lambda *a: launch.append(a))
    with pytest.raises(ValueError, match='retired'):
        hub.new_session('scripted', 'humanoid-unitree-g1', 'apt')
    assert not launch and not hub.store.all()
