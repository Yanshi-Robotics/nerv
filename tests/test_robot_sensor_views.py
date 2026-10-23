"""Offline sensor routing checks; no simulator, hardware or model service is started."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

from nerv.nerve.body import Observation
from nerv.platform.role_turn import RoleTurn
from nerv.platform.session import SessionStore
from nerv.runtime.builtin import BuiltinRuntime


def test_unprepared_simulation_reads_only_declared_robot_cameras(monkeypatch):
    bus = Mock()
    bus.sensor.return_value = (b"fixture-image", "image/png")
    monkeypatch.setattr("nerv.body.bus_zmq.ZmqBus", lambda _: bus)
    runtime = BuiltinRuntime(
        SimpleNamespace(id="fixture", family="arm", configuration={"sensors": [
            {"name": "camera:top", "camera": "top"},
            {"name": "camera:wrist", "camera": "wrist"}]}),
        SimpleNamespace(configuration={"bus_url": "in-memory-fixture"}))
    assert runtime.sensor_names() == ["camera:top", "camera:wrist"]
    assert not bus.mock_calls  # Discovery never connects or samples.
    state, images = runtime.observe()
    assert not state["prepared"] and runtime._impl is None and runtime.actions() == []
    assert [i[0] for i in images] == runtime.sensor_names()
    assert [c.args[0] for c in bus.sensor.call_args_list] == ["top", "wrist"]
    bus.write.assert_not_called()
    bus.spawn.assert_not_called()
    bus.sensor.side_effect = RuntimeError("camera offline")
    state, images = runtime.observe()
    assert images == [] and set(state["sensor_errors"]) == set(runtime.sensor_names())
    runtime.close()
    bus.close.assert_called_once()


def test_agent_observation_is_read_only_and_ignores_ambient_world(tmp_path):
    store = SessionStore(str(tmp_path))
    session, _ = store.new("fixture", "robot", "world", sensors=["camera:overview"])
    robot, world = Mock(), Mock()
    robot.perceive.return_value = Observation({"sensor_errors": {"camera:top": "offline"}}, [])
    hub = SimpleNamespace(store=store, body_client=lambda _: robot, world_client=lambda *_: world,
                          gate=Mock())
    token = threading.Event()
    turn = RoleTurn(hub, session, SimpleNamespace(model="fixture"), lambda _: None, token)
    assert turn.observe().images == []
    assert turn.body is None and not turn.executing()
    robot.post.assert_not_called()
    world.sensor.assert_not_called()
    assert store.get(session.id).messages[-1]["state"]["sensor_errors"]
    token.set()
    assert turn.observe() is None
    assert robot.perceive.call_count == 1
