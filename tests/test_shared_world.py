"""Actual CPU physics, using a small generic fixture (not G1 navigation evidence)."""
import numpy as np
import pytest

from nerv.world.composition import SharedWorld
from nerv.world.mujoco_node import robot_root


@pytest.fixture
def world(tmp_path):
    arena = tmp_path / 'arena.xml'
    arena.write_text('''<mujoco><option gravity="0 0 0"/><worldbody>
      <geom type="plane" size="10 10 .1"/>
      <body name="base" pos="0 0 1"><freejoint/><geom type="sphere" size=".2" mass="1"/>
        <camera name="eye"/><body name="foot" pos="0 0 -.3"><joint name="joint"/>
        <geom type="sphere" size=".1" mass="1"/></body></body></worldbody>
      <actuator><motor joint="joint"/></actuator></mujoco>''')
    owner = SharedWorld(str(arena), [dict(robot_instance_id=name, spawn={'x': x, 'y': 0, 'z': 1, 'yaw': 0})
                                   for name, x in [('A', 0), ('B', 2)]])
    yield owner
    owner.stop()


def attach(view):
    return view.spawn({'joint_names': ['joint'], 'kp': [10], 'kd': [1], 'default_pos': [0], 'torque_limit': [100]})


def test_one_clock_separate_actuators_and_no_second_spawn_reset(world):
    a, b = world.view('A'), world.view('B')
    assert a.model is b.model and a.data is b.data
    with pytest.raises(ValueError, match='one actuated'):
        robot_root(a.model)
    attach(a)
    a.data.qpos[a.free_qadr] = .4
    assert not world.step()  # frozen roster: no falling robot while peers attach
    attach(b)
    assert a.data.qpos[a.free_qadr] == .4
    a.write({'targets': [.5]})
    b.write({'targets': [-.5]})
    world.step(10)
    assert a.read()['t'] == b.read()['t'] > 0
    assert a.read()['joint_pos'][0] > 0 > b.read()['joint_pos'][0]
    assert not set(a.qadr) & set(b.qadr)
    assert not set(a.act_id) & set(b.act_id)
    assert a.sensor_names() == b.sensor_names() == ['camera:eye']
    assert a._camera_for('eye') == 'A/eye'
    with pytest.raises(KeyError):
        a._camera_for('B/eye')
    with pytest.raises(ValueError):
        a.reset()
    with pytest.raises(ValueError):
        attach(a)
    with pytest.raises(ValueError):
        world.view(None)


def test_other_robot_is_visible_to_physical_rays(world):
    a, b = world.view('A'), world.view('B')
    attach(a)
    attach(b)
    hit = a._ray(np.array([0., 0., 1.]), np.array([1., 0., 0.]))
    assert hit == pytest.approx(1.8)
    # Own body is excluded, including children, without excluding another robot.
    assert a._ray(np.array([0., 0., 1.]), np.array([-1., 0., 0.])) == -1


def test_operator_geometry_is_separate_from_sensor_bus(world):
    a,b=world.view('A'),world.view('B')
    attach(a)
    attach(b)
    world.step()
    ga,gb=a.operator_geometry(),b.operator_geometry()
    assert ga['bounds'][1][0] < gb['bounds'][0][0]
    assert ga['robot_instance_id']=='A' and gb['robot_instance_id']=='B'
    assert 'bounds' not in a.read() and 'status' not in a.read()


def test_shared_robot_view_uses_host_cors_origins(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from nerv.world.shared_node import robot_app
    monkeypatch.setenv("NERV_CORS_ORIGINS", "http://127.0.0.1:8100,http://localhost:8100")
    app = FastAPI()
    app.mount("/robots/A", robot_app(world.view("A")))
    with TestClient(app) as client:
        response = client.options("/robots/A/control", headers={
            "Origin": "http://127.0.0.1:8100", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8100"
        denied = client.options("/robots/A/control", headers={
            "Origin": "http://unapproved.example", "Access-Control-Request-Method": "POST"})
        assert denied.status_code == 400


def test_render_preparation_keeps_sensor_sequences_and_physics_failure_separate(world, monkeypatch):
    from nerv.world.shared_node import prepare_rendering
    a, b = world.view('A'), world.view('B')
    attach(a)
    attach(b)
    initial_time = world.data.time
    states = prepare_rendering(world)
    assert {rid: item['state'] for rid, item in states.items()} == {'A':'ready', 'B':'ready'}
    assert a._camera_sequence == b._camera_sequence == 0
    assert world.data.time == initial_time
    pixels, metadata = a.capture_camera('eye')
    assert pixels.size and metadata['source_sequence'] == 1
    assert metadata['robot_instance_id'] == 'A'

    def unavailable(_):
        raise RuntimeError('fixture sensor unavailable')
    monkeypatch.setattr(b.render, 'run', unavailable)
    states = prepare_rendering(world)
    assert states['A']['state'] == 'ready'
    assert states['B']['state'] == 'failed'
    assert states['B']['error_type'] == 'RuntimeError'
    assert a._camera_sequence == 1 and b._camera_sequence == 0
    assert world.step() and world.data.time > initial_time


def test_dynamic_membership_preserves_survivor_state_and_empty_world_clock(world):
    a = world.endpoint('A')
    attach(a)
    attach(world.endpoint('B'))
    world.step(10)
    before = a.read()
    time_before = world.data.time
    world.transact([world.roster[0]], expected_revision=1)
    assert world.topology_revision == 2
    assert world.data.time == time_before
    assert a.read()['joint_pos'] == before['joint_pos']
    assert a.read()['joint_vel'] == before['joint_vel']
    assert a.read()['extra']['topology_revision'] == 2
    for command in ({'targets':[0.]}, {'targets':[0.], 'topology_revision':1}):
        with pytest.raises(ValueError, match='obsolete topology'):
            a.write(command)
    a.write({'targets':[0.], 'topology_revision':2})
    with pytest.raises(ValueError):
        world.endpoint('B').read()
    world.transact([], expected_revision=2)
    assert not world.views
    world.step(10)
    assert world.data.time > time_before
    world.transact([dict(robot_instance_id='C', spawn=dict(x=3,y=0,z=1,yaw=0))], expected_revision=3)
    attach(world.endpoint('C'))
    assert world.step()
    assert world.view('C').model is world.model


def test_failed_topology_preserves_original_model_data_and_revision(world, monkeypatch):
    import nerv.world.composition as composition
    attach(world.view('A'))
    attach(world.view('B'))
    world.step(5)
    model, data, stamp = world.model, world.data, world.data.time
    monkeypatch.setattr(composition, 'migrate_state', lambda *args: (_ for _ in ()).throw(ValueError('fixture failure')))
    with pytest.raises(ValueError, match='fixture failure'):
        world.transact(world.roster[:1], expected_revision=1)
    assert world.model is model and world.data is data and world.data.time == stamp
    assert world.topology_revision == 1 and set(world.views) == {'A','B'}
    assert world.step()


def test_empty_world_can_run_without_robot_clock_owner(world):
    from nerv.world.composition import SharedWorld
    empty = SharedWorld(world.arena, [])
    try:
        assert empty.step(10)
        assert empty.data.time > 0
        empty.start()
        assert empty.running
    finally:
        empty.stop()


def test_physics_evidence_is_step_sampled_and_refuses_topology_during_trial(world):
    from nerv.world.evidence import PhysicsEvidence
    attach(world.endpoint('A'))
    attach(world.endpoint('B'))
    world.evidence = PhysicsEvidence(sample_hz=20,duration_s=.2,max_samples=20)
    with pytest.raises(ValueError, match='recording'):
        world.transact(world.roster[:1], expected_revision=1)
    world.step(120)
    record=world.evidence.read()
    assert record['finished'] and record['physics_events_complete']
    assert record['dropped_samples']==0 and len(record['samples'])>=4
    assert all(b['sim_time']-a['sim_time'] <= .051 for a,b in zip(record['samples'],record['samples'][1:]))
    assert all(set(s['robots'])=={'A','B'} for s in record['samples'])


@pytest.mark.parametrize('margin', [None, .1])
def test_physics_evidence_tracks_a_moving_prop_but_excludes_robot_geometry(world, tmp_path, margin):
    from pathlib import Path
    from nerv.world.evidence import PhysicsEvidence
    arena = tmp_path / 'moving-prop.xml'
    arena.write_text(Path(world.arena).read_text().replace('</worldbody>',
        '<body name="prop" pos="4 0 1"><freejoint name="prop_free"/>'
        '<geom name="prop_shape" size=".1" mass="1"/></body></worldbody>'))
    owner = SharedWorld(str(arena), [dict(robot_instance_id='A', spawn=dict(x=0,y=0,z=1,yaw=0))])
    try:
        attach(owner.view('A'))
        owner.evidence = PhysicsEvidence(sample_hz=25, duration_s=1, max_samples=30, obstacle_margin_m=margin)
        owner.step()
        first = owner.evidence.read()['samples'][0]
        assert first['dynamic_geometry_checked'] == 1
        assert [g['name'] for g in first['dynamic_obstacles']] == (['prop_shape'] if margin is None else [])
        qadr = owner.model.joint('prop_free').qposadr[0]
        owner.data.qpos[qadr] = .35  # Inside clearance margin, without physical penetration.
        owner.step(30)
        latest = owner.evidence.read()['samples'][-1]
        assert latest['dynamic_obstacles'][0]['name'] == 'prop_shape'
        assert latest['dynamic_obstacles'][0]['bounds'][0][0] == pytest.approx(.25)
        assert 'dynamic_obstacles' not in owner.view('A').read()
    finally:
        owner.stop()


def test_motor_bus_serializes_topology_revision_for_existing_connections(world):
    from nerv.body.bus_zmq import ZmqBus
    from nerv.nerve.world import BusCommand
    from nerv.world.mujoco_node import make_bus_handler
    attach(world.endpoint('A'))
    attach(world.endpoint('B'))
    bus=ZmqBus.__new__(ZmqBus)
    bus._req=make_bus_handler(world.endpoint('A'))
    bus.write(BusCommand([0.], topology_revision=1))
    world.transact(world.roster[:1], expected_revision=1)
    with pytest.raises(ValueError, match='obsolete topology'):
        bus.write(BusCommand([0.], topology_revision=1))
    bus.write(BusCommand([.1], topology_revision=2))
    assert world.view('A').targets[0] == .1
