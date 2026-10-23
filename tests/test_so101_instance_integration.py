"""Real CPU simulation through the host; no provider or physical-device calls."""
import pytest

from nerv.platform.hub import Nerv
from nerv.platform.session import SessionStore


def test_tabletop_instances_reset_rebuild_and_reject_stale_control(tmp_path):
    hub = Nerv(store=SessionStore(str(tmp_path / "sessions")))
    from types import SimpleNamespace
    hub._brains["mock"] = SimpleNamespace(model="offline-fixture")
    cases = [("so101-pick-place", {"snack": "snack_rigid"}),
             ("so101-pick-place", {"snack": "soft_snack"}),
             ("so101-pick-place", {"snack": "cloth"}),
             ("so101-sort-snacks", {"chips": "cloth"}),
             ("so101-hanoi-five", {})]
    previous = None
    epochs = set()
    try:
        for world, values in cases:
            session = hub.new_session("mock", "arm-lerobot-so101", world, tools=[], world_configuration=values)
            sid = session["id"]
            assert session["epoch"] not in epochs
            epochs.add(session["epoch"])
            if previous:
                assert not hub.arm(previous, True)["ok"]
                with pytest.raises(ValueError):
                    hub.world_controls.invoke(previous, "reset", {})
            descriptor = hub.world_controls.capabilities(sid)
            assert all(descriptor["state"]["configuration"][key] == value for key, value in values.items())
            observation = hub.perceive(sid)
            assert len(observation.images) == 2 and all(image["png"] for image in observation.images)
            assert hub.arm(sid, True)["armed"]
            for name, args in [("read_joints", {}), ("nudge", {"joint": "shoulder_pan", "delta_deg": 2}),
                               ("set_gripper", {"percent": 60})]:
                results = [event for event in hub.teleop_stream(sid, name, args) if event["type"] == "tool_result"]
                assert results and results[-1]["ok"], results
            assert hub.reset_world(sid)["ok"]
            refreshed = hub.world_controls.capabilities(sid)
            assert refreshed["instance_id"] == descriptor["instance_id"]
            assert refreshed["revision"] == descriptor["revision"] + 1
            assert refreshed["state"] == descriptor["state"]
            with pytest.raises(ValueError, match="revision"):
                hub.world_controls.invoke(sid, "reset", {}, instance_id=session["epoch"], revision=0)
            handle = hub.launcher.nodes["body:arm-lerobot-so101"]
            assert hub.stop_simulation(handle.name, world, handle.meta["epoch"], sid)["ok"]
            assert hub._bound_runtime is None
            assert hub.store.get(sid).status == "frozen"
            previous = sid
    finally:
        hub.shutdown()
