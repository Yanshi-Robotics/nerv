"""Operator-side request accounting; never sent to an acting Agent.

Run/Task journals remain the execution fact source. The protocol receipt journal
also counts denied calls, idempotent retries and interrupted calls, which need not
produce an execution event. A fresh per-trial relay and Run are required.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def audit(participants, started_monotonic, ended_monotonic):
    """Read retained raw evidence and reconcile accepted requests with their Run.

    participants maps robot ID to requests/protocol/journal paths and the bound
    client_id, brain_instance_id and run_id. Clock inputs are operator monotonic
    timestamps on the same host as the two relays, captured at GO and trial end.
    """
    if (len(participants) != 2 or not all(type(t) in (int, float) and math.isfinite(t)
            for t in (started_monotonic, ended_monotonic)) or ended_monotonic < started_monotonic):
        raise ValueError('Two participants and a valid operator monotonic interval are required')
    errors, counts, sources = [], {}, {}
    for rid, participant in participants.items():
        evidence = {}
        sources[rid] = {}
        for kind in ('requests', 'protocol', 'journal'):
            raw = Path(participant[kind]).read_bytes()
            if raw and not raw.endswith(b'\n'):
                raise ValueError('Incomplete evidence journal: ' + kind)
            evidence[kind] = [json.loads(line) for line in raw.splitlines()]
            sources[rid][kind] = hashlib.sha256(raw).hexdigest()
        requests, responses, journal = (evidence[k] for k in ('requests', 'protocol', 'journal'))
        if [r['sequence'] for r in requests] != list(range(1, len(requests) + 1)):
            errors.append(rid + ': non-contiguous request receipts')
        response_by_seq = {r['sequence']: r for r in responses}
        if len(response_by_seq) != len(responses):
            errors.append(rid + ': duplicate response sequence')
        if [r['seq'] for r in journal] != list(range(1, len(journal) + 1)):
            errors.append(rid + ': non-contiguous Run journal')
        motions = [r for r in requests if r['request'].get('operation') == 'action']
        counts[rid] = len(motions)  # Every attempt counts, including retries/denials.
        namespaced = set()
        for request in motions:
            response = response_by_seq.get(request['sequence'], {})
            start, end = request.get('started_monotonic'), response.get('completed_monotonic')
            if (not all(type(t) in (int, float) and math.isfinite(t) for t in (start, end))
                    or not started_monotonic <= start <= end <= ended_monotonic
                    or response.get('request') != request['request']):
                errors.append(rid + ': motion outside trial or incomplete protocol')
            request_id = request['request'].get('arguments', {}).get('request_id')
            if isinstance(request_id, str):
                namespaced.add(hashlib.sha256((participant['brain_instance_id'] + ':' + request_id).encode()).hexdigest())
        for event in journal:
            data = event.get('data', {})
            if event.get('kind') != 'external_agent_activity' or data.get('operation') != 'action':
                continue
            if (data.get('request_id') not in namespaced or data.get('robot_instance_id') != rid
                    or any(data.get(k) != participant[k] for k in ('client_id', 'brain_instance_id', 'run_id'))):
                errors.append(rid + ': execution has no matching bound MCP request')
    return dict(source='mcp_receipts_and_run_task_journal', complete=not errors, errors=errors,
                navigation_wall_seconds=ended_monotonic - started_monotonic,
                motion_requests=counts, evidence_sha256=sources)
