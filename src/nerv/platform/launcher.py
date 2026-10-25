"""Start, attach to and stop nodes. One world node per (world, body); one body node per body;
one tool node per tool. Ports come from NERV_NODE_PORTS; stdout of each node goes to
logs/nodes/<name>.log; the interpreter comes from the registry entry (env-expanded).
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import json
import uuid
from pathlib import Path
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
    bus_url: str = ""          # published world bus, or the bus a body was launched against
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
            raise FileNotFoundError(f"interpreter not found: {py!r} (check the adapter interpreter "
                                    f"or the registry entry's python:)")
        return py

    def _spawn(self, key: str, kind: str, python: str, args: list[str], url: str,
               extra_env: dict | None = None, bus_url: str = "") -> NodeHandle:
        from .runtime_management import check_interpreter
        check_interpreter(python)
        log_path = os.path.join(self.log_dir, re.sub(r'[^A-Za-z0-9_.-]', "_", key) + ".log")
        safe = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "NERV_HOME", "LD_LIBRARY_PATH",
                "CUDA_VISIBLE_DEVICES", "MUJOCO_GL", "DISPLAY", "XAUTHORITY",
                "LIBGL_ALWAYS_SOFTWARE", "__EGL_VENDOR_LIBRARY_FILENAMES", "MUJOCO_EGL_DEVICE_ID", "EGL_PLATFORM")
        env = {k: v for k, v in os.environ.items() if k in safe or k.startswith("LC_")}
        env.update(extra_env or {})
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["PYTHONUTF8"] = "1"
        env["NERV_HOME"] = paths.NERV_HOME
        # nodes must accept requests from the page nerv serve itself hosts, not only the dev origin
        serve_origins = [f"http://127.0.0.1:{config.SERVE_PORT}", f"http://localhost:{config.SERVE_PORT}"]
        env["NERV_CORS_ORIGINS"] = ",".join(dict.fromkeys(config.CORS_ORIGINS + serve_origins))
        logf = open(log_path, "ab")
        proc = subprocess.Popen([python, *args], stdout=logf, stderr=subprocess.STDOUT, env=env,
                                cwd=paths.DATA_ROOT)
        h = NodeHandle(kind=kind, name=key, url=url, bus_url=bus_url, python=python, proc=proc,
                       log_path=log_path)
        self.nodes[key] = h
        try:
            self._wait_health(h)
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=config.NODE_STATUS_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            self.nodes.pop(key, None)
            raise
        finally:
            logf.close()
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
    def check_body_binding(self, body: BodySpec, world: WorldSpec,
                           world_handle: NodeHandle | None = None) -> None:
        """Refuse reuse across worlds before starting nodes or freezing sessions."""
        h = self.nodes.get(f"body:{body.name}")
        if h is None and body.url:
            h = NodeHandle(kind="body", name=f"body:{body.name}",
                           url=body.url.rstrip("/"), attached=True)
        if h is None or not h.alive():
            return
        if h.attached or not h.meta.get("world"):
            self._wait_health(h)
        bound_world = h.meta.get("world")
        if bound_world != world.name:
            raise ValueError(
                f"body `{body.name}` is connected to world `{bound_world or 'unknown'}`; "
                f"cannot reuse it for `{world.name}`. Stop that body node explicitly "
                "before starting a session in another world."
            )
        if world_handle and h.bus_url and h.bus_url != world_handle.bus_url:
            raise ValueError(f"body `{body.name}` is connected to an earlier world bus; "
                             "stop that body node explicitly before reconnecting")

    def ensure_world(self, world: WorldSpec, body: BodySpec, configuration=None) -> NodeHandle | None:
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
        from ..world.configuration import resolve_configuration
        values = resolve_configuration(world.configuration, configuration or {})
        directory = Path(paths.CACHE_DIR) / "instances" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        request = directory / "configuration.json"
        request.write_text(json.dumps({"values": values}))
        py = self._python(world.python)
        url = f"http://{config.NODE_BIND_HOST}:{http_port}"
        bus = f"tcp://{config.NODE_BIND_HOST}:{bus_port}"
        return self._spawn(key, "world", py,
                           ["-m", "nerv.world", "--world", world.name, "--body", body.name,
                            "--http-port", str(http_port), "--bus-port", str(bus_port),
                            "--host", config.NODE_BIND_HOST, "--instance-config", str(request)], url, bus_url=bus)

    def ensure_body(self, body: BodySpec, world: WorldSpec, world_handle: NodeHandle | None) -> NodeHandle:
        self.check_body_binding(body, world, world_handle)
        return self.ensure_runtime(body, world.kind, world, world_handle)

    def ensure_runtime(self, body: BodySpec, mode: str, world=None, world_handle=None) -> NodeHandle:
        key = f"body:{body.name}"
        handle = self.nodes.get(key)
        if handle and handle.alive():
            return handle
        ep = body.buses[mode]
        if not ep.enabled:
            raise ValueError("this environment binding is not configured for the current runtime")
        if not ep.verified and (body.name, mode) not in getattr(self, "supervised_acceptance", set()) and os.environ.get("NERV_ALLOW_UNVERIFIED", "") not in ("1", "true"):
            raise RuntimeError(f"{body.name} {mode} runtime is unverified; enter supervised acceptance from device setup")
        endpoint = ep.url or body.url
        if endpoint:
            handle = NodeHandle(kind="body", name=key, url=endpoint.rstrip("/"), attached=True)
            self.nodes[key] = handle
            self._wait_health(handle)
            self._runtime_protocol(handle)
            return handle
        port = self._free_port()
        from nerv_runtime_sdk.contracts import RobotProfile, EnvironmentBinding
        world_name = world.name if world else ""
        bus_url = world_handle.bus_url if world_handle else ""
        binding = EnvironmentBinding(id=f"{body.name}:{mode}:{world_name}", profile_id=body.name,
                                     mode=mode, provider=ep.provider, world=world_name,
                                     configuration={**ep.settings, "bus_url": bus_url})
        profile = RobotProfile(id=body.name, family=body.family, label=body.label,
                               configuration={**body.model_dump(), "skills": {name: skill.model_dump() for name, skill in body.skills.items() if mode in skill.modes}})
        directory = Path(paths.CACHE_DIR) / "runtime-config"
        directory.mkdir(parents=True, exist_ok=True)
        filename = directory / f"{body.name}.json"
        filename.write_text(json.dumps({"profile": profile.model_dump(), "binding": binding.model_dump(),
                                        "guidance": body.guidance,
                                        "stream_fps": world.physics.get("stream_fps", config.RUNTIME_STREAM_FPS) if world else config.RUNTIME_STREAM_FPS,
                                        "cors_origin": config.CORS_ORIGINS[0]}))
        if ep.command:
            py = self._python(ep.command[0])
            args = [arg.format(host=config.NODE_BIND_HOST, port=port, configuration=str(filename))
                    for arg in ep.command[1:]]
        else:
            py = self._python(ep.python)
            args = ["-m", "nerv_runtime_sdk.worker", "serve", "--provider", ep.provider,
                    "--configuration", str(filename), "--port", str(port), "--host", config.NODE_BIND_HOST]
        if ep.kind == "zmq" and not bus_url:
            raise RuntimeError("a simulation binding requires a world bus")
        declared_env = {}
        for skill in body.skills.values():
            if skill.manifest:
                artifact = json.loads(Path(skill.manifest).read_text(encoding="utf-8"))
                ref = artifact.get("root_env")
                if ref and ref in os.environ:
                    declared_env[ref] = os.environ[ref]
        handle = self._spawn(key, "body", py, args, f"http://{config.NODE_BIND_HOST}:{port}",
                             bus_url=bus_url, extra_env=declared_env)
        self._runtime_protocol(handle)
        if mode == KIND_SIM:
            response = httpx.post(handle.url + "/prepare", timeout=config.NODE_HEALTH_WAIT_S)
            response.raise_for_status()
            if not response.json().get("ok"):
                raise RuntimeError("simulated runtime preparation failed")
        return handle

    @staticmethod
    def _runtime_protocol(handle):
        from nerv_runtime_sdk.contracts import PROTOCOL_MAJOR
        if handle.meta.get("protocol_major") != PROTOCOL_MAJOR:
            raise ValueError("incompatible or undeclared runtime protocol; refusing preparation")

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
                h.proc.wait()
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
