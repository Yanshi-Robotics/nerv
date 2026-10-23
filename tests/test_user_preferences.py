import io
import json
import pytest
from PIL import Image
from nerv import paths
from nerv.platform import user_settings, robot_profile

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'NERV_HOME', str(tmp_path))
    monkeypatch.setattr(paths, 'ENV_FILE', str(tmp_path / '.env'))
    for name in user_settings.FIELDS:
        monkeypatch.delenv(user_settings.config.Settings.model_fields[name].validation_alias, raising=False)
    return tmp_path

def test_snapshot_and_environment_precedence(home, monkeypatch):
    before = user_settings.snapshot()
    user_settings.save({'max_steps': 7})
    assert before['max_steps'] == 60
    assert user_settings.snapshot()['max_steps'] == 7
    monkeypatch.setenv('NERV_MAX_STEPS', '11')
    assert user_settings.snapshot()['max_steps'] == 11
    with pytest.raises(ValueError):
        user_settings.save({'max_steps': 4})
    assert next(p for p in user_settings.describe() if p['key'] == 'max_steps')['editable'] is False
    user_settings.save({}, reset=True)
    assert user_settings.snapshot()['max_steps'] == 11

@pytest.mark.parametrize('value', [{'max_steps': True}, {'max_steps': 1.1}, {'max_steps': 0}, {'turn_time_budget_s': float('nan')}, {'request_token_budget': 20}, {'trust_all': True}])
def test_invalid_preferences_do_not_write(home, value):
    with pytest.raises(ValueError):
        user_settings.save(value)
    assert not user_settings.location().exists()

def test_portrait_and_name_preserve_device(home):
    (home / 'devices').mkdir()
    original = {'id': 'calibration-arm', 'port': 'COM5', 'cameras': {}}
    (home / 'devices/standard.json').write_text(json.dumps(original))
    assert robot_profile.read()['registered']
    robot_profile.save_name('课程机械臂')
    image = Image.new('RGB', (100, 200), 'blue')
    data = io.BytesIO()
    image.save(data, format='PNG')
    result = robot_profile.save_image(data.getvalue())
    assert result['name'] == '课程机械臂'
    assert result['device']['id'] == 'calibration-arm'
    with Image.open(home / 'devices/portrait.webp') as portrait:
        assert portrait.size == (768, 768)
        assert not portrait.getexif()
    with pytest.raises(Exception):
        robot_profile.save_image(b'invalid')
    assert robot_profile.read()['image']
    robot_profile.reset_image()
    assert robot_profile.read()['image'] is None
    assert json.loads((home / 'devices/standard.json').read_text()) == original

def test_session_model_gate_has_no_side_effects(home, monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    from unittest.mock import Mock
    hub = Nerv(store=SessionStore(str(home / 'sessions')))
    monkeypatch.setattr(server, 'nerv', hub)
    launch = Mock(side_effect=AssertionError('must not launch'))
    monkeypatch.setattr(hub.launcher, 'ensure_runtime', launch)
    client = TestClient(server.app)
    for payload in ({}, {'brain': 'operator'}, {'brain': 'missing-model'}, {'brain': 'openai-sol'}):
        assert client.post('/api/sessions', json={'body': 'fixture-arm', 'world': 'fixture-scene', **payload}).status_code == 400
    assert not hub.store.all()
    launch.assert_not_called()
    with pytest.raises(ValueError, match='configured chat model'):
        hub.new_session('operator', None, None)


def test_new_robot_session_cannot_replace_active_task(home, monkeypatch):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    hub = Nerv(store=SessionStore(str(home / 'sessions')))
    session, _ = hub.store.new('operator', 'fixture-arm', None, [])
    monkeypatch.setattr(hub.tasks, 'snapshot', lambda sid: {'task': {'status': 'running'}})
    with pytest.raises(ValueError, match='running task'):
        hub.new_session('operator', 'fixture-arm', None, operator_workspace=True)
    assert hub.store.get(session.id).status == 'active'

def test_concurrent_settings_updates_preserve_fields(home):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(user_settings.save, [{'max_steps': 9}, {'notes_max': 12}]))
    assert user_settings.snapshot()['max_steps'] == 9
    assert user_settings.snapshot()['notes_max'] == 12


def test_offline_action_catalog_matches_runtime(home):
    from nerv_runtime_lerobot.action_catalog import primitive_actions
    from nerv_runtime_lerobot.manage import manage
    from nerv_runtime_lerobot.runtime import LeRobotRuntime
    from types import SimpleNamespace
    settings = {'min_duration_s': .3, 'max_duration_s': 10}
    assert LeRobotRuntime._primitive_actions(SimpleNamespace(settings=settings)) == primitive_actions(settings)
    options = manage('scheme_options', {}, str(home))
    assert {a['name'] for a in options['actions']} == {'move_joints', 'nudge_joints', 'set_gripper'}
    assert not (home / 'devices/standard.json').exists()


def test_discovery_receipt_survives_profile_reload(home, monkeypatch):
    from nerv_runtime_lerobot import discovery
    from nerv_runtime_lerobot.manage import manage
    monkeypatch.setattr(discovery, 'discover', lambda _: {'repair_step': 3, 'cameras': []})
    manage('discover', {}, str(home))
    receipt = robot_profile.read()['last_check']
    assert receipt['repair_step'] == 3
    assert receipt['at'] > 0
    assert receipt['connected'] is False
    assert robot_profile.read()['last_check'] == receipt


@pytest.mark.parametrize('payload', [
    {'body': None, 'world': None, 'mode': 'sim'},
    {'body': ' ', 'world': 'scene', 'mode': 'sim'},
    {'body': 'arm', 'world': None, 'mode': 'sim'},
    {'body': 'arm', 'world': 'scene', 'mode': 'real'},
    {'body': 'arm', 'world': None, 'mode': 'unknown'},
])
def test_public_scene_requirement_precedes_creation(home, monkeypatch, payload):
    from fastapi.testclient import TestClient
    from nerv.platform import server, cli
    from unittest.mock import Mock
    from types import SimpleNamespace
    create = Mock(side_effect=AssertionError('no session creation allowed'))
    monkeypatch.setattr(server.nerv, 'new_session', create)
    monkeypatch.setattr(server, 'list_brains', lambda: [{'name': 'fixture-model'}])
    response = TestClient(server.app).post('/api/sessions', json={'brain': 'fixture-model', **payload})
    assert response.status_code == 400
    with pytest.raises(ValueError):
        cli._new_session(SimpleNamespace(new_session=create), SimpleNamespace(**payload))
    create.assert_not_called()


@pytest.mark.parametrize('mode,world', [('real', None), ('sim', 'fixture-scene')])
def test_public_scene_selection_is_forwarded_unchanged(home, monkeypatch, mode, world):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    from unittest.mock import Mock
    create = Mock(return_value={'id': 'offline-fixture'})
    monkeypatch.setattr(server.nerv, 'new_session', create)
    monkeypatch.setattr(server, 'list_brains', lambda: [{'name': 'fixture-model'}])
    response = TestClient(server.app).post('/api/sessions', json={'brain': 'fixture-model', 'body': 'fixture-arm', 'mode': mode, 'world': world})
    assert response.status_code == 200
    assert create.call_args.args[:3] == ('fixture-model', 'fixture-arm', world)
    assert create.call_args.args[5] == mode
