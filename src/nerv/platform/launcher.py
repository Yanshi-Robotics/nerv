"""Start, attach to and stop nodes. One world node per (world, body); one body node per body;
one tool node per tool. Ports come from NERV_NODE_PORTS; stdout of each node goes to
logs/nodes/<name>.log; the interpreter comes from the registry entry (env-expanded).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field

import httpx

from .. import config, paths
from .registry import BodySpec, Registry, ToolNodeSpec, WorldSpec
from .registry.schema import KIND_SIM


@dataclass
class NodeHandle:
    kind: str                  # world | body | tool
    name: str                  # registry name (world: "<world>/<body>")
    url: str                   # http://host:port
    bus_url: str = ""          # world only
    python: str = ""
    proc: subprocess.Popen | None = None
    log_path: str = ""
    attached: bool = False
    meta: dict = field(default_factory=dict)

    def alive(self) -> bool:
        if self.attached:
            return True
        return self.proc is not None and self.proc.poll() is None


class Launcher:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.nodes: dict[str, NodeHandle] = {}
        self.log_dir = os.path.join(paths.LOGS_DIR, "nodes")
        os.makedirs(self.log_dir, exist_ok=True)

    # -- ports ----------------------------------------------------------------------------
    def _free_port(self) -> int:
        lo, hi = config.port_pool()
        used = set()
        for h in self.nodes.values():
            for u in (h.url, h.bus_url):
                if u and ":" in u:
                    try:
                        used.add(int(u.rsplit(":", 1)[1].rstrip("/")))
                    except ValueError:
                        pass
        for p in range(lo, hi + 1):
            if p in used:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((config.NODE_BIND_HOST, p))
                except OSError:
                    continue
            return p
        raise RuntimeError(f"no free port in NERV_NODE_PORTS={config.NODE_PORTS}")

    # -- spawning ---------------------------------------------------------------------------
    def _python(self, requested: str) -> str:
        py = (requested or "").strip() or sys.executable
        if not os.path.isfile(py):
            raise FileNotFoundError(f"interpreter not found: {py!r} (check NERV_SIM_PYTHON / "
                                    f"NERV_LEROBOT_PYTHON or the registry entry)")
        return py

    def _spawn(self, key: str, kind: str, python: str, args: list[str], url: str,
               extra_env: dict | None = None, bus_url: str = "") -> NodeHandle:
        log_path = os.path.join(self.log_dir, key.replace("/", "_") + ".log")
        env = dict(os.environ)
        env.update(extra_env or {})
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["PYTHONPATH"] = os.pathsep.join(p for p in [os.path.join(paths.REPO_ROOT, "src"),
                                                        env.get("PYTHONPATH", "")] if p)
        logf = open(log_path, "ab")
        proc = subprocess.Popen([python, *args], stdout=logf, stderr=subprocess.STDOUT, env=env,
                                cwd=paths.REPO_ROOT)
        h = NodeHandle(kind=kind, name=key, url=url, bus_url=bus_url, python=python, proc=proc,
                       log_path=log_path)
        self.nodes[key] = h
        self._wait_health(h)
        return h

    def _wait_health(self, h: NodeHandle) -> dict:
        deadline = time.monotonic() + config.NODE_HEALTH_WAIT_S
        last = ""
        while time.monotonic() < deadline:
            if h.proc is not None and h.proc.poll() is not None:
                tail = self._tail(h.log_path)
                raise RuntimeError(f"{h.kind} node {h.name} exited with code {h.proc.returncode}.\n{tail}")
            try:
                r = httpx.get(h.url + "/health", timeout=config.NODE_PROBE_TIMEOUT)
                if r.status_code == 200:
                    h.meta = r.json()
                    return h.meta
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = type(e).__name__
            time.sleep(0.3)
        raise TimeoutError(f"{h.kind} node {h.name} did not answer /health within "
                           f"{config.NODE_HEALTH_WAIT_S:g}s ({last}).\n{self._tail(h.log_path)}")

    @staticmethod
    def _tail(path: str, n: int = 25) -> str:
        try:
            with open(path, "rb") as f:
                return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])
        except Exception:
            return ""

    # -- public -----------------------------------------------------------------------------
    def ensure_world(self, world: WorldSpec, body: BodySpec) -> NodeHandle | None:
        if world.kind != KIND_SIM:
            return None                     # reality needs no process
        key = f"world:{world.name}/{body.name}"
        h = self.nodes.get(key)
        if h and h.alive():
            return h
        if world.url:
            h = NodeHandle(kind="world", name=key, url=world.url.rstrip("/"), attached=True,
                           bus_url=config.env_expand(os.environ.get("NERV_WORLD_BUS_URL", "")))
            self.nodes[key] = h
            self._wait_health(h)
            h.bus_url = h.bus_url or str(h.meta.get("bus_url", ""))
            return h
        http_port, bus_port = self._free_port(), None
        # reserve http first so bus gets a different port
        placeholder = NodeHandle(kind="world", name=key, url=f"http://{config.NODE_BIND_HOST}:{http_port}")
        self.nodes[key] = placeholder
        bus_port = self._free_port()
        del self.nodes[key]
        py = self._python(world.python or config.SIM_PYTHON)
        url = f"http://{config.NODE_BIND_HOST}:{http_port}"
        bus = f"tcp://{config.NODE_BIND_HOST}:{bus_port}"
        return self._spawn(key, "world", py,
                           ["-m", "nerv.world", "--world", world.name, "--body", body.name,
                            "--http-port", str(http_port), "--bus-port", str(bus_port),
                            "--host", config.NODE_BIND_HOST], url, bus_url=bus)

    def ensure_body(self, body: BodySpec, world: WorldSpec, world_handle: NodeHandle | None) -> NodeHandle:
        key = f"body:{body.name}"
        h = self.nodes.get(key)
        if h and h.alive():
            return h
        ep = body.buses[world.kind]
        if not ep.verified and os.environ.get("NERV_ALLOW_UNVERIFIED", "") not in ("1", "true"):
            raise RuntimeError(f"body `{body.name}` bus endpoint for {world.kind} is marked unverified; "
                               f"set NERV_ALLOW_UNVERIFIED=1 to launch it anyway")
        if body.url:
            h = NodeHandle(kind="body", name=key, url=body.url.rstrip("/"), attached=True)
            self.nodes[key] = h
            self._wait_health(h)
            return h
        port = self._free_port()
        default_py = config.SIM_PYTHON if world.kind == KIND_SIM else config.LEROBOT_PYTHON
        py = self._python(ep.python or default_py)
        args = ["-m", "nerv.body", "--body", body.name, "--world", world.name, "--kind", world.kind,
                "--port", str(port), "--host", config.NODE_BIND_HOST]
        if ep.kind == "zmq":
            if not world_handle or not world_handle.bus_url:
                raise RuntimeError("a zmq body endpoint needs a running world node with a bus")
            args += ["--bus", world_handle.bus_url]
        return self._spawn(key, "body", py, args, f"http://{config.NODE_BIND_HOST}:{port}")

    def ensure_tool(self, tool: ToolNodeSpec) -> NodeHandle:
        key = f"tool:{tool.name}"
        h = self.nodes.get(key)
        if h and h.alive():
            return h
        if tool.url:
            h = NodeHandle(kind="tool", name=key, url=tool.url.rstrip("/"), attached=True)
            self.nodes[key] = h
            self._wait_health(h)
            return h
        port = self._free_port()
        py = self._python(tool.python)
        return self._spawn(key, "tool", py, ["-m", "nerv.tool", "--tool", tool.module or tool.name,
                                             "--port", str(port), "--host", config.NODE_BIND_HOST],
                           f"http://{config.NODE_BIND_HOST}:{port}")

    def stop(self, key: str) -> bool:
        h = self.nodes.pop(key, None)
        if h is None:
            return False
        if h.proc is not None and h.proc.poll() is None:
            h.proc.terminate()
            try:
                h.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                h.proc.kill()
        return True

    def stop_all(self) -> None:
        for key in list(self.nodes):
            self.stop(key)

    def list(self) -> list[dict]:
        out = []
        for h in self.nodes.values():
            out.append({"key": h.name, "kind": h.kind, "url": h.url, "bus_url": h.bus_url,
                        "alive": h.alive(), "attached": h.attached, "log": h.log_path,
                        "python": h.python, "meta": h.meta})
        return out
