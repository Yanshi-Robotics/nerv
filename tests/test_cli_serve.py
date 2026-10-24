"""Browser launch follows successful HTTP startup and never gates serving."""
import asyncio
import threading
from urllib.request import urlopen

import pytest
import uvicorn
from fastapi import FastAPI

from nerv.platform import cli, serving


@pytest.mark.parametrize(("host", "expected"), [
    ("127.0.0.1", "127.0.0.1"), ("0.0.0.0", "127.0.0.1"),
    ("::", "[::1]"), ("::1", "[::1]"), ("localhost", "localhost"),
])
def test_browser_url(host, expected):
    assert serving.browser_url(host, 8100) == f"http://{expected}:8100/"


@pytest.mark.parametrize("arguments", [[], ["serve"], ["serve", "--no-browser", "--port", "8100"]])
def test_cli_browser_option(arguments, monkeypatch):
    captured = []
    monkeypatch.setattr(serving.BrowserServer, "run", lambda self: captured.append(self))
    assert cli.main(arguments) == 0
    assert captured[0].open_browser == ("--no-browser" not in arguments)
    if "--port" in arguments:
        assert captured[0].config.port == 8100


@pytest.mark.parametrize("open_browser", [True, False])
def test_browser_launch_after_http_ready(open_browser, monkeypatch):
    app = FastAPI()

    @app.get("/")
    def root():
        return {"ready": True}

    opened = threading.Event()
    responses = []

    def browser(url):
        with urlopen(url, timeout=5) as response:
            responses.append(response.read())
        opened.set()
        return True

    monkeypatch.setattr(serving.webbrowser, "open_new_tab", browser)

    async def scenario():
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        config.load()
        server = serving.BrowserServer(config, open_browser=open_browser)
        server.lifespan = config.lifespan_class(config)
        await server.startup()
        try:
            if open_browser:
                assert await asyncio.to_thread(opened.wait, 5)
                assert responses == [b'{"ready":true}']
            else:
                assert not opened.is_set()
        finally:
            await server.shutdown()

    asyncio.run(scenario())


def test_failed_startup_does_not_open_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(serving.webbrowser, "open_new_tab", lambda url: opened.append(url))

    async def failed_startup(self, sockets=None):
        raise SystemExit(1)

    monkeypatch.setattr(uvicorn.Server, "startup", failed_startup)
    with pytest.raises(SystemExit):
        asyncio.run(serving.BrowserServer(uvicorn.Config(FastAPI())).startup())
    assert opened == []


@pytest.mark.parametrize("raises", [False, True])
def test_browser_unavailable_keeps_server_usable(monkeypatch, caplog, raises):
    def unavailable(url):
        if raises:
            raise OSError("no desktop")
        return False

    monkeypatch.setattr(serving.webbrowser, "open_new_tab", unavailable)
    serving._open_browser("http://127.0.0.1:8100/")
    assert "Open http://127.0.0.1:8100/ manually" in caplog.text


def test_repeated_interrupt_preserves_cleanup_and_restores_handlers(monkeypatch):
    import signal
    server = serving.BrowserServer(uvicorn.Config(FastAPI()), open_browser=False)
    original = signal.getsignal(signal.SIGINT)
    notices = []
    monkeypatch.setattr(serving.logger, "info", notices.append)
    with server.capture_signals():
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
        assert server.should_exit and not server.force_exit
    assert signal.getsignal(signal.SIGINT) == original
    assert 'Stopping NERV' in notices[0]
    assert 'will not skip cleanup' in notices[1]


def test_shutdown_notifies_observers_before_lifespan_cleanup(monkeypatch):
    order = []

    async def drain(self, sockets=None):
        order.append('drain-and-cleanup')

    monkeypatch.setattr(uvicorn.Server, 'shutdown', drain)
    server = serving.BrowserServer(uvicorn.Config(FastAPI()), open_browser=False,
                                   before_shutdown=lambda: order.append('cancel-observers'))
    asyncio.run(server.shutdown())
    assert order == ['cancel-observers', 'drain-and-cleanup']


def test_request_shutdown_cancels_both_roles_without_killing_nodes(tmp_path):
    from unittest.mock import Mock
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    hub = Nerv(store=SessionStore(str(tmp_path)))
    chat, task = threading.Event(), threading.Event()
    hub._runs['chat'] = chat
    hub.tasks._tokens['task'] = task
    hub.launcher.stop_all = Mock()
    hub.request_shutdown()
    assert chat.is_set() and task.is_set()
    hub.launcher.stop_all.assert_not_called()
    hub.shutdown()
    hub.launcher.stop_all.assert_called_once()

    with pytest.raises(ValueError, match="shutting down"):
        hub._claim_run('new', None)
    with pytest.raises(ValueError, match="shutting down"):
        hub.tasks.start('new', 'goal')


def test_shutdown_failure_is_not_hidden(monkeypatch):
    async def fail(self, sockets=None):
        raise RuntimeError('cleanup failed')
    monkeypatch.setattr(uvicorn.Server, 'shutdown', fail)
    server = serving.BrowserServer(uvicorn.Config(FastAPI()), open_browser=False)
    with pytest.raises(RuntimeError, match='cleanup failed'):
        asyncio.run(server.shutdown())


def test_shutdown_rejects_new_http_mutations(monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    stopping = threading.Event()
    stopping.set()
    monkeypatch.setattr(server, '_shutdown_requested', stopping)
    result = TestClient(server.app).post('/api/sessions', json={})
    assert result.status_code == 503
    assert result.json()['detail'] == 'NERV is shutting down'
