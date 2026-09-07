"""MJPEG cadence includes rendering, encoding and consumer backpressure; no sockets."""
import asyncio
from types import SimpleNamespace

import pytest

from nerv.world import mujoco_node


@pytest.mark.parametrize('work_s, expected_sleep', [(0.025, 0.075), (0.14, 0.0)])
def test_stream_frame_period_includes_work(monkeypatch, work_s, expected_sleep):
    clock = [0.0]
    sleeps = []
    frames = []

    def render():
        clock[0] += work_s
        frames.append(len(frames))
        return bytes([frames[-1]]), clock[0]

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(mujoco_node.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(mujoco_node, '_jpeg', lambda frame, quality: frame)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    sim = SimpleNamespace(world_name='test', phys={
        'stream_fps': 10, 'jpeg_quality': 70, 'third_person': True}, render_chase=render)
    app = mujoco_node.build_app(sim, [])
    endpoint = next(r.endpoint for r in app.routes if r.path == '/stream')

    async def run():
        response = await endpoint()
        first = await anext(response.body_iterator)
        second = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first != second
    assert sleeps == pytest.approx([expected_sleep])


def test_failed_frame_is_not_replaced_or_busy_retried(monkeypatch):
    clock = [0.0]
    sleeps = []
    calls = [0]

    def render():
        calls[0] += 1
        clock[0] += 0.02
        if calls[0] == 1:
            raise RuntimeError('temporary render failure')
        return b'new-image', clock[0]

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(mujoco_node.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(mujoco_node, '_jpeg', lambda frame, quality: frame)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    sim = SimpleNamespace(world_name='test', phys={
        'stream_fps': 10, 'jpeg_quality': 70, 'third_person': True}, render_chase=render)
    endpoint = next(r.endpoint for r in mujoco_node.build_app(sim, []).routes if r.path == '/stream')

    async def run():
        response = await endpoint()
        frame = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return frame

    assert b'new-image' in asyncio.run(run())
    assert calls[0] == 2
    assert sleeps == pytest.approx([0.08])
