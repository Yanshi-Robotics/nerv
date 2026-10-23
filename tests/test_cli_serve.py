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
