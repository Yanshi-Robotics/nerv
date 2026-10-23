"""SessionStore-compatible task view with all events persisted in the parent journal."""
from __future__ import annotations

import dataclasses
import time
import uuid
from pathlib import Path

from ..harness.scoped_memory import ScopedMemory


class TaskStore:
    def __init__(self, parent, sid, task_id, model, goal):
        self.parent, self.sid, self.task_id = parent, sid, task_id
        self.model, self.goal = model, goal
        self.root = parent.root
        self._memory = ScopedMemory(parent.memory(sid), task_id)

    def memory(self, _sid):
        return self._memory

    def exists(self, _sid):
        return self.parent.exists(self.sid)

    def is_active(self, _sid):
        return self.parent.is_active(self.sid)

    def get(self, _sid):
        state = {"core_task": self.goal, "notes": []}
        for e in self._memory.events():
            if e["kind"] == "register":
                state[e["data"]["key"]] = e["data"]["value"]
        state["core_task"] = self.goal
        return dataclasses.replace(self.parent.get(self.sid), brain=self.model,
                                   messages=self._memory.messages(), **state)

    def append(self, _sid, entry):
        self._memory.append("message", {**entry, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})

    def append_perception(self, _sid, state, images):
        refs = []
        directory = Path(self.root) / self.sid / "imgs" / self.task_id
        directory.mkdir(parents=True, exist_ok=True)
        for img in images or []:
            if img.get("png"):
                path = directory / (uuid.uuid4().hex + ".png")
                path.write_bytes(img["png"])
                refs.append({"name": img.get("name", ""), "ref": str(path.relative_to(self.root))})
        self.append(self.sid, {"role": "perception", "images": refs, "state": state,
                               "image_ref": refs[0]["ref"] if refs else None})

    def set_core_task(self, _sid, task):
        self._memory.append("register", {"key": "core_task", "value": task})

    def set_notes(self, _sid, notes):
        self._memory.append("register", {"key": "notes", "value": notes})
