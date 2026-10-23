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
        assert client.post('/api/sessions', json=payload).status_code == 400
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
