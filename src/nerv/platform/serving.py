"""Open the local operator interface only after the HTTP server has started."""
from __future__ import annotations

from contextlib import contextmanager
import signal
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
    def __init__(self, config: uvicorn.Config, *, open_browser: bool = True, before_shutdown=None):
        super().__init__(config)
        self.open_browser = open_browser
        self.before_shutdown = before_shutdown
        self._repeat_notice = False

    @contextmanager
    def capture_signals(self):
        # NERV owns orderly terminal shutdown. Do not replay handled signals after
        # lifespan cleanup: asyncio would turn a normal Ctrl+C into a traceback.
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        previous = {sig: signal.signal(sig, self.handle_exit) for sig in signals}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def handle_exit(self, sig, frame):
        if not self.should_exit:
            logger.info("Stopping NERV. Closing requests and managed processes; please wait.")
            self.should_exit = True
        elif not self._repeat_notice:
            self._repeat_notice = True
            logger.info("NERV is still shutting down. Repeated Ctrl+C will not skip cleanup.")

    async def shutdown(self, sockets=None):
        if self.before_shutdown is not None:
            self.before_shutdown()
        await super().shutdown(sockets=sockets)


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
