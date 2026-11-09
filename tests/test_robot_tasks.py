"""0.9.1 task lifecycle through external execution, with no internal model fixture."""
import threading
from unittest.mock import Mock
import pytest
from test_project_mcp_integration import integrated  # noqa: F401


@pytest.fixture
def task_host(integrated):  # noqa: F811
    f = integrated
    f.projects.create_run(f.project['id'])
    s = f.projects.require_session(f.project['id'])
    f.hub.store.set_armed(s.id, True)
    return f.hub, s


def test_reply_cancel_does_not_cancel_external_task(task_host):
    hub, s = task_host
    hub.tasks.start(s.id, 'goal')
    token = threading.Event()
    hub._runs[s.id] = token
    hub.stop_reply(s.id)
    assert token.is_set() and not hub.tasks._tokens[s.id].is_set()
    assert hub.tasks.snapshot(s.id)['task']['status'] == 'running'
    assert hub.tasks.cancel(s.id)['ok']
    assert hub.tasks.snapshot(s.id)['task']['status'] == 'cancelled'


def test_external_goal_is_immutable_and_restart_never_replays(task_host):
    hub, s = task_host
    hub.tasks.start(s.id, 'first')
    with pytest.raises(ValueError, match='immutable'):
        hub.tasks.update(s.id, 1, goal='second')
    from nerv.platform.robot_tasks import RobotTasks
    recovered = RobotTasks(hub)
    assert recovered.snapshot(s.id)['task']['status'] == 'interrupted'
    assert not recovered._threads


def test_retired_model_entrypoints_never_construct_a_provider(task_host):
    hub, s = task_host
    # The production methods must reject even a historical model ID.
    from nerv.platform.hub import Nerv
    with pytest.raises(ValueError, match='retired'):
        Nerv.new_role_model(hub, 'old-model')
    with pytest.raises(ValueError, match='retired'):
        hub.brain('old-model')
    hub.tasks.start(s.id, 'external goal')
    hub.new_role_model.assert_not_called()


def test_cancelled_request_cannot_launch_task(task_host):
    hub, s = task_host
    result = hub.tasks.start(s.id, 'old', current=lambda: False)
    assert not result['ok']
    hub.claim_robot.assert_not_called()


def test_failed_control_claim_has_no_worker(task_host, monkeypatch):
    hub, s = task_host
    monkeypatch.setattr(hub, 'claim_robot', Mock(side_effect=RuntimeError('refused')))
    result = hub.tasks.start(s.id, 'goal')
    assert not result['ok']
    assert hub.tasks.snapshot(s.id)['task']['status'] == 'failed'
    assert not hub.tasks._threads


def test_unconfirmed_stop_blocks_replacement_task(task_host, monkeypatch):
    hub, s = task_host
    hub.tasks.start(s.id, 'first')
    monkeypatch.setattr(hub, 'stop_robot', Mock(return_value={'ok':False}))
    assert not hub.tasks.cancel(s.id)['ok']
    with pytest.raises(ValueError, match='stopping'):
        hub.tasks.start(s.id, 'second')
    monkeypatch.setattr(hub, 'stop_robot', Mock(return_value={'ok':True}))
    assert hub.tasks.cancel(s.id)['ok']
    assert hub.tasks.start(s.id, 'second')['ok']


def test_late_activity_cannot_change_cancelled_task(task_host):
    hub, s = task_host
    state = hub.tasks.start(s.id, 'goal')['data']
    hub.tasks.cancel(s.id)
    before = hub.tasks.snapshot(s.id)['cursor']
    hub.tasks.event(s.id, state['task_id'], state['version'], {'type':'reply','text':'late success'})
    assert hub.tasks.snapshot(s.id)['cursor'] == before
    assert hub.tasks.snapshot(s.id)['task']['outcome'] == 'cancelled'
