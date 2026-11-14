"""Image identity/time belong to scene capture, not later delivery or a state poll."""
import threading
from types import SimpleNamespace

import numpy as np
import mujoco

from nerv.body.bus_zmq import ZmqBus
from nerv.nerve import wire
from nerv.world.mujoco_node import WorldSim


def test_capture_metadata_and_pixels_share_one_scene_snapshot():
    world = WorldSim.__new__(WorldSim)
    world.model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
      <body name="robot-A"><freejoint/><geom size=".1"/>
      <camera name="robot-A/head"/></body></worldbody></mujoco>''')
    world.data = mujoco.MjData(world.model)
    world.base_body = world.model.body('robot-A').id
    world.data.time = 10.
    mujoco.mj_forward(world.model, world.data)
    world._lock = threading.RLock()
    world._camera_sequence = 0
    world.epoch = 'epoch'
    world.robot_instance_id = 'robot-A'
    world.owner = SimpleNamespace(world_instance_id='world', topology_revision=3)
    world._camera_for = lambda _: 'robot-A/head'
    renderer = SimpleNamespace(update_scene=lambda *_a, **_k: None)
    def render():
        world.data.time = 12.  # delivery/render completion must not retimestamp pixels
        return np.zeros((2, 2, 3), dtype=np.uint8)
    renderer.render = render
    world.render = SimpleNamespace(run=lambda fn: fn(renderer), _w=2, _h=2)
    pixels, source = world.capture_camera('head')
    assert pixels.shape == (2, 2, 3)
    assert source['captured_sim_time'] == 10
    assert source['calibration']['sample_sim_time'] == 10
    assert source['calibration']['valid'] is True
    assert source['calibration']['resolution_px'] == [2, 2]
    assert source['source_sequence'] == 1
    assert source['robot_instance_id'] == 'robot-A'
    assert source['world_instance_id'] == 'world' and source['topology_revision'] == 3
    _, second = world.capture_camera('head')
    assert second['captured_sim_time'] == 12 and second['source_sequence'] == 2


def test_bus_preserves_source_metadata_and_legacy_sensor_tuple():
    source = dict(captured_sim_time=3.4, source_sequence=8, robot_instance_id='A')
    bus = ZmqBus.__new__(ZmqBus)
    bus._camera = SimpleNamespace(request=lambda _: dict(data=wire.b64e(b'pixels'), mime='image/png', source=source))
    assert bus.sensor_frame('head') == (b'pixels', 'image/png', source)
    assert bus.sensor('head') == (b'pixels', 'image/png')
