"""Rejected/replayed requests consume budget even without another execution."""
import hashlib
import json

from tools.acceptance.audit_meeting import audit


def fixture(tmp_path):
    participants = {}
    for rid in ('A', 'B'):
        directory = tmp_path / rid
        directory.mkdir()
        participant = dict(client_id='client-' + rid, brain_instance_id='brain-' + rid, run_id='run-' + rid)
        calls = [dict(sequence=i, started_monotonic=10 + i,
                      request=dict(operation='action', arguments={'request_id': 'same-retry'})) for i in (1, 2)]
        replies = [{**call, 'completed_monotonic': call['started_monotonic'] + .1} for call in calls]
        event = dict(seq=1, kind='external_agent_activity', data={**participant,
            'robot_instance_id': rid, 'operation': 'action',
            'request_id': hashlib.sha256((participant['brain_instance_id'] + ':same-retry').encode()).hexdigest()})
        for kind, rows in [('requests', calls), ('protocol', replies), ('journal', [event])]:
            path = directory / (kind + '.jsonl')
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            participant[kind] = path
        participants[rid] = participant
    return participants


def test_retries_and_denials_count_before_run_task_deduplication(tmp_path):
    participants = fixture(tmp_path)
    result = audit(participants, 10, 14, termination_reason='agents_finished')
    assert result['complete'] and result['motion_requests'] == {'A': 2, 'B': 2}
    # Removing the sole execution is a fully denied request pair, not zero calls.
    participants['A']['journal'].write_text('')
    assert audit(participants, 10, 14, termination_reason='agents_finished')['motion_requests']['A'] == 2


def test_interrupted_call_and_late_call_prevent_valid_budget(tmp_path):
    participants = fixture(tmp_path)
    path = participants['A']['protocol']
    original = path.read_text()
    path.write_text(original.splitlines()[0] + '\n')
    assert not audit(participants, 10, 14, termination_reason='agents_finished')['complete']
    path.write_text(original)
    assert not audit(participants, 10, 12, termination_reason='agents_finished')['complete']


def test_unlogged_or_cross_instance_execution_invalidates_audit(tmp_path):
    participants = fixture(tmp_path)
    path = participants['A']['journal']
    event = json.loads(path.read_text())
    event['data']['robot_instance_id'] = 'B'
    path.write_text(json.dumps(event) + '\n')
    assert not audit(participants, 10, 14, termination_reason='agents_finished')['complete']


def test_stop_failure_cannot_be_cleared_by_a_later_successful_response(tmp_path):
    participants = fixture(tmp_path)
    path = participants['A']['protocol']
    replies = [json.loads(line) for line in path.read_text().splitlines()]
    for reply, stopped in zip(replies, [False, True]):
        reply['result'] = {'content': [dict(type='text', text=json.dumps({'data': {'stop_confirmed': stopped}}))]}
    path.write_text(''.join(json.dumps(row) + '\n' for row in replies))
    result = audit(participants, 10, 14, termination_reason='agents_finished')
    assert result['complete']
    assert result['terminal_violations'] == [dict(robot_instance_id='A', source='mcp_response',
                                                sequence=1, condition='stop_unconfirmed')]


def test_journal_retains_timeout_even_if_client_response_has_no_details(tmp_path):
    participants = fixture(tmp_path)
    path = participants['B']['journal']
    with path.open('a') as stream:
        stream.write(json.dumps(dict(seq=2,kind='scoped_event',data=dict(kind='message',
            data=dict(role='tool',data=dict(timed_out=True,stop_confirmed=True))))) + '\n')
    result = audit(participants, 10, 14, termination_reason='agents_finished')
    assert result['terminal_violations'][0]['condition'] == 'timeout'


def test_operator_abort_cannot_be_reclassified_by_later_stable_geometry(tmp_path):
    participants = fixture(tmp_path)
    result = audit(participants, 10, 14, termination_reason='collector_error')
    assert result['terminal_violations'][0]['condition'] == 'trial_not_completed'


def test_interrupted_motion_remains_terminal_even_after_confirmed_stopping(tmp_path):
    participants = fixture(tmp_path)
    path = participants['A']['protocol']
    replies = [json.loads(line) for line in path.read_text().splitlines()]
    replies[0]['result'] = {'content': [dict(type='text', text=json.dumps({'data': {
        'execution_status': 'interrupted', 'reason': 'context_invalidated', 'stop_confirmed': True}}))]}
    path.write_text(''.join(json.dumps(row) + '\n' for row in replies))
    result = audit(participants, 10, 14, termination_reason='agents_finished')
    assert result['terminal_violations'] == [dict(robot_instance_id='A', source='mcp_response',
                                                sequence=1, condition='execution_interrupted')]
