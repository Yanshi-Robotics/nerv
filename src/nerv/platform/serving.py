"""Open the local operator interface only after the HTTP server has started."""
from __future__ import annotations

import logging
import threading
import webbrowser

import uvicorn

logger = logging.getLogger("uvicorn.error")


def browser_url(host: str, port: int) -> str:
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def _open_browser(url: str) -> None:
    try:
        if webbrowser.open_new_tab(url):
            return
    except Exception:
        # A missing or broken desktop browser must not stop the HTTP service.
        logger.debug("Browser launch failed", exc_info=True)
    logger.warning("Could not open a browser. Open %s manually.", url)


class BrowserServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, *, open_browser: bool = True):
        super().__init__(config)
        self.open_browser = open_browser

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if not self.started or self.should_exit:
            return
        port = self.servers[0].sockets[0].getsockname()[1]
        url = browser_url(self.config.host, port)
        logger.info("NERV interface: %s", url)
        if self.open_browser:
            # Desktop launchers can block; keep HTTP serving and shutdown independent.
            threading.Thread(target=_open_browser, args=(url,), daemon=True,
                             name="nerv-browser").start()
