"""The body camera budget includes acquisition and does not block the HTTP loop."""
import asyncio
import threading

import pytest

from nerv.body import node
from nerv.body.skills import StopFlag
from test_body_node import FakeArm


@pytest.mark.parametrize("work_s,expected_sleep", [(0.025, 0.075), (0.14, 0.0)])
def test_camera_work_counts_toward_period_and_runs_off_loop(monkeypatch, work_s, expected_sleep):
    clock, sleeps, threads = [0.0], [], []
    main_thread = threading.get_ident()
    impl = FakeArm()
    def frame():
        threads.append(threading.get_ident())
        clock[0] += work_s
        return str(len(threads)).encode()
    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
    impl.stream_jpeg = frame
    monkeypatch.setattr(node.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(asyncio, "sleep", sleep)
    body = node.BodyNode(impl, guidance="test", stop=StopFlag(), armed=False, stream_fps=10)
    endpoint = next(r.endpoint for r in node.build_app(body, []).routes if r.path == "/stream")
    async def receive():
        response = await endpoint()
        first = await anext(response.body_iterator)
        second = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return first, second
    first, second = asyncio.run(receive())
    assert first != second
    assert sleeps == pytest.approx([expected_sleep])
    assert all(t != main_thread for t in threads)


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")])
def test_invalid_frame_rate_refused(fps):
    with pytest.raises(ValueError, match="stream_fps"):
        node.BodyNode(FakeArm(), guidance="test", stop=StopFlag(), armed=False, stream_fps=fps)
