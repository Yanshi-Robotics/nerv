"""0.9.1 task lifecycle through external execution, with no internal model fixture."""
import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_new_task_cannot_inherit_previous_execution_or_stopping_evidence(task_host):
    hub, s = task_host
    first = hub.tasks.start(s.id, 'first')['data']
    hub.tasks._save(s.id, execution={'id': 'old-execution'}, end_reason='old-end')
    assert hub.tasks.cancel(s.id)['ok']
    old = [e for e in hub.store.memory(s.id).events() if e['kind'] == 'robot_task']
    second = hub.tasks.start(s.id, 'second')['data']
    assert second['task_id'] != first['task_id']
    assert not {'execution', 'stop_result', 'end_reason'} & second.keys()
    assert hub.tasks.snapshot(s.id)['task'] == second
    assert [e for e in hub.store.memory(s.id).events() if e['kind'] == 'robot_task'][:len(old)] == old


def test_late_watchdog_cannot_cancel_a_replacement_task_by_old_id(task_host):
    hub, s = task_host
    first = hub.tasks.start(s.id, 'first')['data']
    assert hub.tasks.cancel(s.id)['ok']
    second = hub.tasks.start(s.id, 'second')['data']
    hub.stop_robot.reset_mock()
    assert hub.tasks.cancel(s.id, expected_task_id=first['task_id'])['superseded']
    hub.stop_robot.assert_not_called()
    assert hub.tasks.snapshot(s.id)['task']['task_id'] == second['task_id']
    assert not hub.tasks._tokens[s.id].is_set()


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


def test_unknown_execution_requires_transport_and_runtime_termination_before_recovery(task_host):
    hub, s = task_host
    hub.tasks.start(s.id, 'first')
    worker = Mock()
    worker.is_alive.return_value = True
    hub.tasks._unknown_executions[s.id] = {'token': hub.tasks._tokens[s.id], 'worker': worker}
    robot = hub.body_client(s.body)
    robot.post.return_value = {'ok': True, 'running': False}
    robot.post.side_effect = None
    assert not hub.tasks.cancel(s.id)['ok']
    assert hub.tasks.stopping(s.id)
    worker.is_alive.return_value = False
    robot.post.return_value = {'ok': True}  # A missing execution state is still unknown.
    assert not hub.tasks.cancel(s.id)['ok']
    with pytest.raises(ValueError, match='stopping'):
        hub.tasks.start(s.id, 'blocked')
    robot.post.return_value = {'ok': True, 'running': False}
    assert hub.tasks.cancel(s.id)['ok']
    assert not hub.tasks.stopping(s.id)
    assert hub.tasks.start(s.id, 'recovered')['ok']


def test_delayed_old_deadline_cannot_cancel_a_replacement_task(task_host, monkeypatch):
    hub, s = task_host
    original_get = hub.schemes.get
    first_scheme = original_get(s.control_scheme, s.control_scheme_revision)
    reads = 0
    def scheme(*args):
        nonlocal reads
        reads += 1
        return first_scheme.model_copy(update={'max_runtime_s': .05 if reads == 1 else 60})
    monkeypatch.setattr(hub.schemes, 'get', scheme)
    entered, release = threading.Event(), threading.Event()
    original_cancel = hub.tasks.cancel
    def delayed_cancel(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original_cancel(*args, **kwargs)
    monkeypatch.setattr(hub.tasks, 'cancel', delayed_cancel)
    try:
        assert hub.tasks.start(s.id, 'first')['ok']
        first_thread = hub.tasks._threads[s.id]
        assert entered.wait(1)
        assert original_cancel(s.id)['ok']
        second = hub.tasks.start(s.id, 'second')['data']
        second_token = hub.tasks._tokens[s.id]
        release.set()
        first_thread.join(1)
        assert not first_thread.is_alive()
        current = hub.tasks.snapshot(s.id)['task']
        assert current['task_id'] == second['task_id'] and current['status'] == 'running'
        assert not second_token.is_set()
    finally:
        release.set()
        original_cancel(s.id)


def test_cancelled_old_deadline_cannot_invalidate_replacement_before_claim(task_host, monkeypatch):
    hub, s = task_host
    original_get, original_cancel = hub.schemes.get, hub.tasks.cancel
    scheme = original_get(s.control_scheme, s.control_scheme_revision)
    expired, resume_expiry = threading.Event(), threading.Event()
    resolving, resume_resolution = threading.Event(), threading.Event()
    reads = 0
    def get_scheme(*args):
        nonlocal reads
        reads += 1
        if reads == 2:
            resolving.set()
            assert resume_resolution.wait(2)
        return scheme.model_copy(update={'max_runtime_s': .05 if reads == 1 else 60})
    def delayed_expiry(*args, **kwargs):
        expired.set()
        assert resume_expiry.wait(2)
        return original_cancel(*args, **kwargs)
    monkeypatch.setattr(hub.schemes, 'get', get_scheme)
    monkeypatch.setattr(hub.tasks, 'cancel', delayed_expiry)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert hub.tasks.start(s.id, 'first')['ok']
            first_thread = hub.tasks._threads[s.id]
            assert expired.wait(1)
            assert original_cancel(s.id)['ok']
            replacement = pool.submit(hub.tasks.start, s.id, 'second')
            assert resolving.wait(1)
            resume_expiry.set()
            first_thread.join(1)
            assert not first_thread.is_alive()
            resume_resolution.set()
            assert replacement.result(1)['ok']
            assert hub.tasks.snapshot(s.id)['task']['status'] == 'running'
            assert not hub.tasks._tokens[s.id].is_set()
        finally:
            resume_expiry.set()
            resume_resolution.set()
            original_cancel(s.id)
