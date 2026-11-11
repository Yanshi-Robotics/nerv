"""A prepared worker at a reused URL must not inherit an empty manifest."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from nerv.platform import client
from nerv.nerve.body import CAPABILITIES_URI


def test_prepare_and_epoch_change_refresh_manifest(monkeypatch):
    phase = {'prepare_state': 'unprepared', 'epoch': 'first', 'manifest_revision': 0}
    names = []
    class Session:
        async def list_tools(self):
            return NS(tools=[NS(name=n, annotations=None, description=n, inputSchema={}) for n in names])
        async def list_resources(self):
            return NS(resources=[NS(uri=CAPABILITIES_URI)])
        async def read_resource(self, uri):
            return NS(contents=[NS(text=json.dumps(dict(phase, robot_actions={n: {} for n in names})))])
        async def list_prompts(self):
            return NS(prompts=[])
    async def with_session(url, op, timeout):
        return await op(Session())
    monkeypatch.setattr(client, 'with_session', with_session)
    monkeypatch.setattr(client, 'run_sync', lambda coro, timeout: asyncio.run(coro))
    body = client.RemoteBody('fixture', 'http://unused.invalid')
    try:
        with pytest.raises(ValueError, match='not ready'):
            body.capabilities()
        phase.update(prepare_state='ready', manifest_revision=2)
        names.append('move')
        caps = body.raw_capabilities()
        assert [t.name for t in caps.operations] == ['move']
        assert caps.runtime_epoch == 'first'
        phase.update(epoch='second', manifest_revision=4)
        names.append('turn')
        caps = body.raw_capabilities()
        assert caps.runtime_epoch == 'second'
        assert len(caps.operations) == 2
    finally:
        body.close()
