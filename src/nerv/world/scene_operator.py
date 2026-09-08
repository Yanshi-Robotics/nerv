"""Leased operator scene controls. All methods run under the world's physics lock.

This is a control-plane camera and force controller, never a robot skill or sensor.
The scene library supplies facilities and task semantics; this module owns transport
independent selection, camera geometry and fail-closed lease handling.
"""
from __future__ import annotations

import math
import secrets
import time

import mujoco
import numpy as np


DEFAULTS = {
    "scene_lease_s": 2.0,              # abandoned pointer/connection cannot keep applying force
    "scene_reach_m": 2.0,              # matches the native scene inspection reach
    "scene_focus_m": 1.6,              # close enough to select the focused part
    "scene_camera_min_m": 0.15,
    "scene_camera_max_m": 12.0,
    "scene_still_speed_m_s": 0.12,      # conservative acquisition check after the body stops
    "scene_still_angular_rad_s": 0.3,
    "scene_task_period_s": 0.05,       # task evaluation at 20 Hz, force control at physics rate
}


def vector(value, length=3):
    result = np.asarray(value, dtype=float)
    if result.shape != (length,) or not np.isfinite(result).all():
        raise ValueError(f"Expected {length} finite coordinates")
    return result


class SceneOperator:
    def __init__(self, sim, runtime, clock=time.monotonic):
        self.sim, self.runtime, self.clock = sim, runtime, clock
        self.config = {**DEFAULTS, **sim.phys}
        self.lease = None
        self.selection = None
        self.last_reason = "idle"
        self._task_at = -math.inf
        self.camera = mujoco.MjvCamera()
        self.camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.camera.azimuth, self.camera.elevation = 90.0, -12.0
        self.camera.distance = self.config["scene_focus_m"]
        self.camera.lookat[:] = (0, 0, 1)
        self._grab_distance = 1.0

    def cancel(self, reason="cancelled"):
        self.runtime.interaction.cancel()
        self.selection = None
        self.last_reason = reason

    def revoke(self, reason="released"):
        self.cancel(reason)
        self.lease = None

    def active(self):
        if self.lease and self.clock() >= self.lease["expires"]:
            self.revoke("lease_expired")
        return self.lease is not None

    def _stable(self):
        sim = self.sim
        state = sim.status()
        if not sim.spawned or state.get("fallen") or not sim.physics_alive():
            return False
        if sim.has_free_base:
            speed = sim.data.qvel[sim.free_dadr:sim.free_dadr + 6]
            return (np.linalg.norm(speed[:3]) <= self.config["scene_still_speed_m_s"]
                    and np.linalg.norm(speed[3:]) <= self.config["scene_still_angular_rad_s"])
        return True

    def acquire(self, session, owner, epoch):
        if epoch != self.sim.epoch or not session or not owner:
            raise ValueError("The session or world changed")
        if self.active():
            raise ValueError("Another scene test already owns this world")
        if not self._stable():
            raise ValueError("Wait for the robot to stop and stand steadily")
        self.cancel()
        self.lease = {"session": session, "owner": owner, "token": secrets.token_urlsafe(24),
                      "epoch": epoch, "expires": self.clock() + self.config["scene_lease_s"]}
        return {"token": self.lease["token"], "lease_seconds": self.config["scene_lease_s"],
                "epoch": epoch, "owner": owner}

    def check(self, session, owner, token, epoch, renew=False):
        if not self.active() or any(self.lease[k] != v for k, v in
                (("session", session), ("owner", owner), ("token", token), ("epoch", epoch))):
            raise ValueError("Scene control expired or belongs to another operator")
        if epoch != self.sim.epoch:
            self.revoke("world_changed")
            raise ValueError("The world changed")
        if renew:
            self.lease["expires"] = self.clock() + self.config["scene_lease_s"]

    def tick(self):
        if self.active():
            if self.sim.status().get("fallen"):
                self.revoke("robot_fallen")
            else:
                self.runtime.interaction.update()
        if self.sim.data.time >= self._task_at:
            self.runtime.update_task()
            self._task_at = float(self.sim.data.time) + self.config["scene_task_period_s"]

    def reset(self):
        self.revoke("scene_reset")
        self.runtime.reset()
        self._task_at = -math.inf

    def _basis(self):
        az, el = math.radians(self.camera.azimuth), math.radians(self.camera.elevation)
        forward = np.array([math.cos(el)*math.cos(az), math.cos(el)*math.sin(az), math.sin(el)])
        right = np.array([math.sin(az), -math.cos(az), 0.0])
        up = np.cross(right, forward)
        origin = self.camera.lookat - forward * self.camera.distance
        return origin, forward, right, up

    def ray(self, xy):
        xy = vector(xy, 2)
        if np.any(np.abs(xy) > 1):
            raise ValueError("Pointer is outside the image")
        origin, forward, right, up = self._basis()
        half = math.tan(math.radians(float(self.sim.model.vis.global_.fovy))/2)
        aspect = self.sim.phys["render_width"] / self.sim.phys["render_height"]
        direction = forward + xy[0]*half*aspect*right + xy[1]*half*up
        return origin, direction / np.linalg.norm(direction)

    def view(self, values):
        if "lookat" in values:
            self.camera.lookat[:] = vector(values["lookat"])
        for key in ("azimuth", "elevation", "distance"):
            if key not in values:
                continue
            value = float(values[key])
            if not math.isfinite(value):
                raise ValueError("Camera values must be finite")
            if key == "elevation":
                value = np.clip(value, -85, 85)
            if key == "distance":
                value = np.clip(value, self.config["scene_camera_min_m"], self.config["scene_camera_max_m"])
            setattr(self.camera, key, value)
        self.selection = None

    def focus(self, facility_id):
        item = next((f for f in self.runtime.catalogue()["facilities"] if f["id"] == facility_id), None)
        if item is None:
            raise ValueError("Unknown facility")
        anchor = item["anchor"]
        body = item.get("body")
        if body:
            bid = mujoco.mj_name2id(self.sim.model, mujoco.mjtObj.mjOBJ_BODY, body)
            if bid >= 0:
                # A movable object's anchor follows its actual geometry, not its initial position.
                ids = np.flatnonzero(self.sim.model.geom_bodyid == bid)
                if len(ids):
                    anchor = np.mean(self.sim.data.geom_xpos[ids], axis=0)
        self.view({"lookat": anchor, "distance": self.config["scene_focus_m"],
                   "azimuth": 90.0, "elevation": -12.0})

    def select(self, xy):
        origin, direction = self.ray(xy)
        hit = self.runtime.interaction.select(origin, direction, self.config["scene_reach_m"])
        self.selection = hit
        self.last_reason = "selected" if hit else "occluded_or_out_of_reach"
        return hit

    def command(self, action, values):
        ix = self.runtime.interaction
        if action == "focus":
            self.focus(values["facility"])
        elif action == "view":
            self.view(values)
        elif action == "select":
            self.select(values["xy"])
        elif action in ("joint", "grab"):
            # Re-raycast on the command, so stale selection cannot reach through a moved door.
            hit = self.select(values["xy"])
            if not hit:
                raise ValueError("The part is occluded or out of reach")
            if action == "joint":
                name = values["joint"]
                if name not in hit["names"]:
                    raise ValueError("Select the actual moving part before operating it")
                ix.command(name, float(values["fraction"]))
            else:
                ix.grab(hit["body"], hit["point"])
                self._grab_distance = float(hit["distance"])
        elif action == "drag":
            if ix.held is None:
                raise ValueError("No object is held")
            origin, direction = self.ray(values["xy"])
            depth = float(values.get("distance", self._grab_distance))
            if not math.isfinite(depth) or not 0 < depth <= self.config["scene_reach_m"]:
                raise ValueError("The drag target is out of reach")
            ix.move_grab(origin + direction*depth)
        elif action == "release":
            ix.release()
        elif action == "cancel":
            self.cancel()
        else:
            raise ValueError("Unknown scene action")

    def state(self):
        active = self.active()
        ix = self.runtime.interaction
        selection = None if self.selection is None else {
            "names": self.selection["names"], "body": int(self.selection["body"]),
            "point": np.asarray(self.selection["point"]).tolist(), "distance": float(self.selection["distance"])}
        return {"available": True, "active": active, "epoch": self.sim.epoch,
                "lease_seconds": self.config["scene_lease_s"],
                "session": self.lease["session"] if active else None,
                "owner": self.lease["owner"] if active else None,
                "phase": self.runtime.phase, "phases": list(self.runtime.phases),
                "selection": selection, "held": int(ix.held[0]) if ix.held else None,
                "joints": {name: ix.status(name) for name in ix.joints},
                "task": self.runtime.task_status(), "reason": self.last_reason,
                "view": {"lookat": self.camera.lookat.tolist(), "azimuth": float(self.camera.azimuth),
                         "elevation": float(self.camera.elevation), "distance": float(self.camera.distance)}}
