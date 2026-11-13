"""Separate uncertain transport observations from confirmed instance failure."""
from __future__ import annotations

import time
import httpx


def classify(health, expected_epoch, *, alive=True, status_code=200):
    if not alive:
        return 'failed', 'process_exited'
    if status_code != 200 or not isinstance(health, dict):
        return 'unknown', 'probe_unavailable'
    epoch = health.get('epoch')
    if isinstance(epoch, str) and epoch and expected_epoch and epoch != expected_epoch:
        return 'failed', 'epoch_changed'
    if health.get('ok') is False:
        return 'failed', 'node_reported_unhealthy'
    if health.get('ok') is True and isinstance(epoch, str) and epoch:
        return 'healthy', 'same_instance'
    return 'unknown', 'incomplete_health'


def probe(url, expected_epoch, timeout, *, alive=True):
    started = time.monotonic()
    health, status, exception = None, None, None
    if alive:
        try:
            response = httpx.get(url, timeout=timeout)
            status = getattr(response, 'status_code', 200)
            if status == 200:
                health = response.json()
        except (httpx.HTTPError, ValueError) as error:
            exception = type(error).__name__
    state, reason = classify(health, expected_epoch, alive=alive, status_code=status)
    return dict(state=state, reason=reason, started_monotonic=started,
                completed_monotonic=time.monotonic(), observed_at=time.time(),
                process_alive=bool(alive), status_code=status, exception=exception,
                expected_epoch=expected_epoch,
                observed_epoch=health.get('epoch') if isinstance(health, dict) else None)
