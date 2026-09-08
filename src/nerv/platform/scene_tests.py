"""Operator-only scene test ownership. No facility or robot-specific knowledge."""
from __future__ import annotations

import threading
import time

from .. import config
from . import interrupt
from .session import log


class SceneTests:
    def __init__(self, hub):
        self.hub = hub
        self._owners = {}
        self._lock = threading.Lock()

    def _client(self, sid):
        session = self.hub.store.get(sid)
        if not session.world or not session.body or self.hub._control_error(session):
            raise ValueError("This session no longer controls a simulated world")
        spec = self.hub.registry.world(session.world)
        if spec.kind != "sim":
            raise ValueError("Scene tests are only available in simulation")
        client = self.hub.world_client(session.world, session.body)
        if client is None:
            raise ValueError("The world is unavailable")
        return session, client

    def blocks(self, body):
        with self._lock:
            now = time.monotonic()
            for sid, item in list(self._owners.items()):
                if item["expires"] <= now:
                    self._owners.pop(sid, None)
            return any(item["body"] == body for item in self._owners.values())

    def read(self, sid, resource):
        _session, client = self._client(sid)
        return client.scene_get(resource)

    def acquire(self, sid, owner):
        session, client = self._client(sid)
        if not owner or len(owner) > 128:
            raise ValueError("An operator identity is required")
        # Reserve locally before waiting on the body. No slow IO holds this lock.
        with self._lock:
            if any(item["body"] == session.body and item["expires"] > time.monotonic()
                   for item in self._owners.values()):
                raise ValueError("Another scene test already owns this body")
            reservation = {"body": session.body, "owner": owner,
                           "expires": time.monotonic() + config.SCENE_ENTRY_TIMEOUT_S}
            self._owners[sid] = reservation
        try:
            interrupt.request(sid)
            body = self.hub.body_client(session.body)
            if body is None:
                raise ValueError("The body is unavailable")
            body.post("/stop")
            deadline = time.monotonic() + config.SCENE_ENTRY_TIMEOUT_S
            while time.monotonic() < deadline:
                self._client(sid)  # detect a freeze or epoch change during acquisition
                status = body.status() or {}
                if not status.get("running_skill") and not status.get("held") and not status.get("policy_error"):
                    disarmed = self.hub.arm(sid, False)
                    if not disarmed.get("ok"):
                        raise ValueError(disarmed.get("message", "Could not disarm the body"))
                    result = client.scene_post("acquire", {"session": sid, "owner": owner, "epoch": session.epoch})
                    if result.get("ok"):
                        with self._lock:
                            cancelled = self._owners.get(sid) is not reservation
                            if not cancelled:
                                reservation.update(token=result["token"], epoch=result["epoch"],
                                    expires=time.monotonic() + float(result["lease_seconds"]))
                        if cancelled:
                            client.scene_post("release", {"session": sid, **{k: result[k]
                                for k in ("owner", "token", "epoch")}})
                            raise ValueError("Scene acquisition was interrupted")
                        self._client(sid)
                        log.record("operator", {"session": sid, "event": "scene_test_acquired"})
                        return result
                time.sleep(config.SCENE_ENTRY_POLL_S)
            raise ValueError("The robot did not settle; scene testing was not started")
        except Exception:
            self.cancel(sid, expected=reservation)
            raise

    def credentials(self, sid, payload):
        session, client = self._client(sid)
        with self._lock:
            item = self._owners.get(sid)
            if (not item or item["expires"] <= time.monotonic() or any(
                    item.get(k) != payload.get(k) for k in ("owner", "token", "epoch"))):
                raise ValueError("Scene control expired or belongs to another operator")
        if session.armed:
            self.cancel(sid)
            raise ValueError("Scene control ended because the body was armed")
        return client, {"session": sid, **{k: payload[k] for k in ("owner", "token", "epoch")}}

    def action(self, sid, operation, payload):
        client, credentials = self.credentials(sid, payload)
        result = client.scene_post(operation, {**payload, **credentials})
        if operation == "heartbeat" and result.get("ok"):
            with self._lock:
                item = self._owners.get(sid)
                if item:
                    item["expires"] = time.monotonic() + float(result["lease_seconds"])
        if operation == "release" or not result.get("ok") and operation == "heartbeat":
            self.cancel(sid)
        return result

    def cancel(self, sid, expected=None):
        with self._lock:
            if expected is not None and self._owners.get(sid) is not expected:
                return
            item = self._owners.pop(sid, None)
        if not item or not item.get("token"):
            return
        session = self.hub.store.get(sid)
        client = self.hub.world_client(session.world, session.body)
        if client:
            client.scene_post("release", {"session": sid, **{k: item[k] for k in ("owner", "token", "epoch")}})

    def interrupt(self, sid):
        # Fire-and-forget cancellation keeps emergency controls independent of scene HTTP latency.
        threading.Thread(target=self.cancel, args=(sid,), daemon=True).start()
        interrupt.request(sid)
