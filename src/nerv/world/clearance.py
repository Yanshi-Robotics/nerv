"""Sensor-like motion clearance against physical scenery, never scene labels or truth poses.

The caller owns the physics lock. A private ray model excludes visual finishes and the
entire controlled body while retaining the indices/transforms of the live MjData.
"""
from __future__ import annotations

import copy
import math

import mujoco
import numpy as np

# Bound work per request, including malformed or excessively dense remote requests.
MAX_RAYS = 4096
RAY_MASK = np.array([1, 0, 0, 0, 0, 0], dtype=np.uint8)


class ClearanceScanner:
    def __init__(self, model, robot_root_body: int):
        self.model, self.root = model, robot_root_body
        self.self_geoms = model.body_rootid[model.geom_bodyid] == robot_root_body
        self._signature = None
        self._view = None
        # Prepare before the body's policy starts; copying a textured model can be
        # much slower than the repeated ray measurements themselves.
        self._physical_view()

    def _physical_view(self):
        m = self.model
        signature = (m.geom_contype.tobytes(), m.geom_conaffinity.tobytes())
        physical = (m.geom_contype != 0) | (m.geom_conaffinity != 0)
        if signature != self._signature:
            self._view = copy.copy(m)
            self._view.geom_group[:] = np.where(physical & ~self.self_geoms, 0, 5)
            # Rendering transparency must never make a load-bearing collider disappear.
            self._view.geom_rgba[:, 3] = 1
            self._view.mat_rgba[:, 3] = 1
            self._signature = signature
        return physical

    @staticmethod
    def _number(query, name, *, positive=False):
        value = float(query[name])
        if not math.isfinite(value) or value < 0 or (positive and value == 0):
            raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
        return value

    def measure(self, data, query: dict) -> dict:
        """Return remaining travel from the body's envelope, not a ray from its centre.

        Height offsets and support differences refer to the measured lowest sole. Turning
        uses a conservative disk around the current physical body, including arm extension.
        """
        try:
            return self._measure(data, query)
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            return {"valid": False, "reason": "unavailable", "message": str(error)}

    def _measure(self, data, query):
        mode = query["motion"]
        if mode not in ("forward", "turn"):
            raise ValueError("motion must be forward or turn")
        horizon = self._number(query, "horizon_m", positive=True)
        spacing = self._number(query, "sample_spacing_m", positive=True)
        vertical_spacing = self._number(query, "height_spacing_m", positive=True)
        margin = self._number(query, "envelope_margin_m")
        drop = self._number(query, "max_drop_m")
        rise = self._number(query, "max_rise_m")
        probe_margin = self._number(query, "support_probe_margin_m", positive=True)
        seam = self._number(query, "support_seam_tolerance_m")
        if seam >= spacing / 2:
            raise ValueError("support seam tolerance must be below half the sample spacing")
        heights = np.asarray(query["height_offsets_m"], dtype=float)
        if (heights.ndim != 1 or not 1 <= len(heights) <= 16
                or not np.isfinite(heights).all() or np.any(heights <= 0)):
            raise ValueError("height_offsets_m needs 1–16 finite positive heights")
        names = query["support_body_names"]
        if not isinstance(names, list) or not names:
            raise ValueError("support_body_names is required")
        m = self.model
        support = set()
        for name in names:
            bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)
            if bid <= 0 or m.body_rootid[bid] != self.root:
                raise ValueError("support bodies must belong to the controlled body")
            support.add(bid)
        physical = self._physical_view()
        own = np.flatnonzero(physical & self.self_geoms)
        if not len(own):
            raise ValueError("the body has no physical envelope")
        rot = data.geom_xmat[own].reshape(-1, 3, 3)
        centres = data.geom_xpos[own] + np.einsum("nij,nj->ni", rot, m.geom_aabb[own, :3])
        halves = np.einsum("nij,nj->ni", np.abs(rot), m.geom_aabb[own, 3:])
        sole = []
        for i, gid in enumerate(own):
            bid = int(m.geom_bodyid[gid])
            while bid and bid not in support:
                bid = int(m.body_parentid[bid])
            if bid in support:
                sole.append(centres[i, 2] - halves[i, 2])
        if not sole:
            raise ValueError("support bodies have no physical geometry")
        floor_z = float(min(sole))
        base = data.xpos[self.root].copy()
        root_rot = data.xmat[self.root].reshape(3, 3)
        yaw = math.atan2(root_rot[1, 0], root_rot[0, 0])
        forward = np.array([math.cos(yaw), math.sin(yaw), 0.0])
        left = np.array([-math.sin(yaw), math.cos(yaw), 0.0])
        basis = np.array([forward, left, [0., 0., 1.]])
        local_rot = np.einsum("ij,njk->nik", basis, rot)
        local_c = (centres - base) @ basis.T
        local_h = np.einsum("nij,nj->ni", np.abs(local_rot), m.geom_aabb[own, 3:])
        low, high = (local_c - local_h).min(axis=0), (local_c + local_h).max(axis=0)
        # Sparse named levels alone miss thin tables/shelves between the levels.
        # Fill up to the measured body's top with the configured vertical spacing.
        body_top = float(np.max(centres[:, 2] + halves[:, 2])) - floor_z + margin
        height_count = max(2, math.ceil(body_top / vertical_spacing) + 1)
        if height_count > MAX_RAYS:
            raise ValueError("clearance query exceeds the ray budget")
        heights = np.unique(np.concatenate([heights, np.linspace(min(heights), body_top, height_count)]))
        front = max(0.0, float(high[0])) + margin
        side_low, side_high = float(low[1]) - margin, float(high[1]) + margin
        # AABB corners conservatively cover every physical part's horizontal projection.
        radius = float(np.max(np.linalg.norm(np.abs(local_c[:, :2]) + local_h[:, :2], axis=1))) + margin
        gid = np.zeros(1, dtype=np.int32)
        rays = 0

        def ray(origin, direction):
            nonlocal rays
            rays += 1
            if rays > MAX_RAYS:
                raise ValueError("clearance query exceeds the ray budget")
            return float(mujoco.mj_ray(self._view, data, origin, direction, RAY_MASK, 1, -1, gid))

        def samples(start, end):
            count = max(2, int(math.ceil((end-start) / spacing)) + 1)
            if count > MAX_RAYS:
                raise ValueError("clearance query exceeds the ray budget")
            return np.linspace(start, end, count)

        result = {"valid": True, "reason": "clear", "travel_clearance_m": horizon,
                  "support_drop_m": 0.0, "support_rise_m": 0.0, "turn_clear": True}

        def blocked(distance, reason):
            if distance <= result["travel_clearance_m"]:
                result["travel_clearance_m"] = max(0.0, float(distance))
                result["reason"] = reason
                if mode == "turn":
                    result["turn_clear"] = False

        def support_at(x, y, travel):
            def difference_at(x, y):
                origin = base + forward*x + left*y
                origin[2] = floor_z + rise + probe_margin
                distance = ray(origin, np.array([0., 0., -1.]))
                return None if distance < 0 else rise + probe_margin - distance

            difference = difference_at(x, y)
            if seam and (difference is None or not -drop <= difference <= rise):
                # Exact box seams can miss at floating-point boundaries. Accept only
                # when ALL four tiny surrounding samples support the sole; never
                # average across an actual edge, step or basin.
                nearby = [difference_at(x+dx*seam, y+dy*seam)
                          for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1))]
                if all(v is not None and -drop <= v <= rise for v in nearby):
                    difference = min(nearby)
            if difference is None:
                result["support_drop_m"] = None
                blocked(travel, "drop")
                return
            if result["support_drop_m"] is not None:
                result["support_drop_m"] = max(result["support_drop_m"], -difference)
            result["support_rise_m"] = max(result["support_rise_m"], difference)
            if difference < -drop:
                blocked(travel, "drop")
            elif difference > rise:
                blocked(travel, "rise")

        if mode == "forward":
            lateral = samples(side_low, side_high)
            for height in heights:
                for y in lateral:
                    origin = base + left*y
                    origin[2] = floor_z + height
                    distance = ray(origin, forward)
                    if 0 <= distance < front + horizon:
                        blocked(distance-front, "obstacle")
            for x in samples(0, front+horizon):
                for y in lateral:
                    # The edge can lie anywhere since the previous support sample.
                    support_at(x, y, max(0., x-front-spacing))
        else:
            angles = samples(0, 2*math.pi*radius) / max(radius, np.finfo(float).eps)
            for angle in angles[:-1]:
                dx, dy = math.cos(angle), math.sin(angle)
                direction = forward*dx + left*dy
                for height in heights:
                    origin = base.copy()
                    origin[2] = floor_z + height
                    distance = ray(origin, direction)
                    if 0 <= distance < radius:
                        blocked(0, "obstacle")
                for r in samples(0, radius):
                    support_at(r*dx, r*dy, 0)
        result["sample_count"] = rays
        return result
