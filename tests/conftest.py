import socket
import threading
import time

import httpx
import pytest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, port: int):
    """Run a FastAPI app on a background thread; returns (server, thread) after /health answers."""
    import uvicorn
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return srv, th
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("server did not come up")


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("NERV_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NERV_TRUST_ALL", "1")
    yield
