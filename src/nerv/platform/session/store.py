"""Sessions and their local memory. A session = brain × body × world, plus what happened.

- One active session per body node; creating a new session on the same body freezes the
  old one (read-only). Sessions do not share memory; the brain can be swapped mid-session.
- A session records the world node's epoch. If the world restarts, the session becomes
  `reconnect_required` instead of silently continuing on a different physics.
- Everything is one JSON file per session; perception images land as PNG files next to it.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field

from ... import config, paths
from ...nerve import operator as op

_ROOT = os.getenv("NERV_SESSION_ROOT", paths.SESSIONS_DIR)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _gen_id() -> str:
    return f"s_{time.time_ns()}"


@dataclass
class Session:
    id: str
    brain: str
    body: str | None            # None = conversation only
    world: str | None
    status: str                 # active | frozen | reconnect_required
    created_at: str
    title: str
    sensors: list = field(default_factory=list)     # ambient streams the brain also sees
    tools: list = field(default_factory=list)       # tool nodes mounted
    armed: bool = False                             # hardware may move only when True
    epoch: str = ""                                 # world node epoch at bind time
    core_task: str = ""
    notes: list = field(default_factory=list)
    messages: list = field(default_factory=list)

    def summary(self) -> dict:
        return {"id": self.id, "brain": self.brain, "body": self.body, "world": self.world,
                "status": self.status, "created_at": self.created_at, "title": self.title,
                "sensors": list(self.sensors), "tools": list(self.tools), "armed": self.armed,
                "epoch": self.epoch, "core_task": self.core_task, "notes": list(self.notes)}


class SessionStore:
    def __init__(self, root: str = _ROOT) -> None:
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, sid: str) -> str:
        return os.path.join(self.root, f"{sid}.json")

    def _imgs_dir(self, sid: str) -> str:
        d = os.path.join(self.root, sid, "imgs")
        os.makedirs(d, exist_ok=True)
        return d

    def save(self, s: Session) -> None:
        tmp = self._path(s.id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(s), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path(s.id))

    def load(self, sid: str) -> Session:
        with open(self._path(sid), encoding="utf-8") as f:
            return Session(**json.load(f))

    def exists(self, sid: str) -> bool:
        return os.path.exists(self._path(sid))

    def all(self) -> list[Session]:
        out = []
        for fn in os.listdir(self.root):
            if fn.endswith(".json"):
                try:
                    out.append(self.load(fn[:-5]))
                except Exception:
                    pass
        out.sort(key=lambda s: s.created_at, reverse=True)
        return out

    def new(self, brain: str, body: str | None, world: str | None, sensors: list | None = None,
            tools: list | None = None, epoch: str = "") -> tuple[Session, list[str]]:
        frozen: list[str] = []
        if body:
            for s in self.all():
                if s.body == body and s.status == op.SESSION_ACTIVE:
                    s.status = op.SESSION_FROZEN
                    s.armed = False
                    self.save(s)
                    frozen.append(s.id)
        s = Session(id=_gen_id(), brain=brain, body=body, world=world, status=op.SESSION_ACTIVE,
                    created_at=_now(), title="(new session)", sensors=list(sensors or []),
                    tools=list(tools or []), epoch=epoch)
        self.save(s)
        return s, frozen

    def delete(self, sid: str) -> bool:
        existed = self.exists(sid)
        try:
            os.remove(self._path(sid))
        except FileNotFoundError:
            pass
        d = os.path.join(self.root, sid)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        return existed

    def get(self, sid: str) -> Session:
        return self.load(sid)

    def list(self) -> list[dict]:
        return [s.summary() for s in self.all()]

    def append(self, sid: str, entry: dict) -> None:
        s = self.load(sid)
        entry.setdefault("ts", _now())
        s.messages.append(entry)
        if s.title == "(new session)" and entry.get("role") == "user":
            s.title = entry["text"][:config.TITLE_MAX_LEN]
        self.save(s)

    def append_perception(self, sid: str, state: dict, images: list[dict]) -> None:
        s = self.load(sid)
        n = sum(1 for m in s.messages if m.get("role") == "perception")
        refs: list[dict] = []
        for i, img in enumerate(images or []):
            if not img.get("png"):
                continue
            nm = (img.get("name") or f"cam{i}").replace("/", "_").replace(":", "_")
            fn = f"{n}_{nm}.png"
            with open(os.path.join(self._imgs_dir(sid), fn), "wb") as f:
                f.write(img["png"])
            refs.append({"name": img.get("name", ""), "ref": f"{sid}/imgs/{fn}"})
        s.messages.append({"role": "perception", "image_ref": refs[0]["ref"] if refs else None,
                           "images": refs, "state": state, "ts": _now()})
        self.save(s)

    def set_brain(self, sid: str, brain: str) -> None:
        s = self.load(sid)
        s.brain = brain
        self.save(s)

    def set_status(self, sid: str, status: str) -> None:
        s = self.load(sid)
        s.status = status
        self.save(s)

    def set_armed(self, sid: str, armed: bool) -> None:
        s = self.load(sid)
        s.armed = bool(armed)
        self.save(s)

    def set_core_task(self, sid: str, task: str) -> None:
        s = self.load(sid)
        s.core_task = task
        self.save(s)

    def set_notes(self, sid: str, notes: list) -> None:
        s = self.load(sid)
        s.notes = list(notes)
        self.save(s)

    def is_active(self, sid: str) -> bool:
        return self.load(sid).status == op.SESSION_ACTIVE
