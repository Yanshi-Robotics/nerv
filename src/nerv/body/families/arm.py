"""The arm family: a position-controlled arm with a gripper.

Primitives (write a target, interpolate, settle, report measured joints):
  move_joints(targets, duration_s)   absolute targets in degrees, any subset of joints
  nudge(joint, delta_deg)            one joint, relative
  set_gripper(percent)               0 = closed … 100 = open
Skills: none built in. A learned grasp trained with LeRobot plugs in through body.yaml `skills`
once one exists (the runner is here; the policy is not).

Units on the bus are radians for every joint, gripper included; degrees and percent are the
brain-facing units. Limits come from the bus at spawn (MJCF ranges / calibration).
"""
from __future__ import annotations

import math
import threading
import time

from ...nerve.body import KIND_PRIMITIVE, KIND_READ
from ...nerve.world import PD_POSITION_ACTUATOR, ActuatorSpec, BusCommand, MotorBus
from ..node import jpeg_from_png
from ..skills import SkillRunner

DEG = math.pi / 180.0


def build(spec: dict, bus: MotorBus, runner: SkillRunner):
    return ArmBody(spec, bus, runner)


class ArmBody:
    family = "arm"

    def __init__(self, spec: dict, bus: MotorBus, runner: SkillRunner) -> None:
        self.name = spec["name"]
        self.version = str(spec.get("version", "0"))
        self.spec = spec
        self.bus = bus
        self.runner = runner
        act = spec.get("actuators") or {}
        self.joints: list[str] = list(act.get("joints") or [])
        self.gripper: str = act.get("gripper", "gripper")
        self.rate_hz = float(act.get("rate_hz", 50))
        self.max_step_deg = float(act.get("max_step_deg", 30))
        self.min_duration_s = float(act.get("min_duration_s", 0.3))
        self.max_duration_s = float(act.get("max_duration_s", 10))
        self.default_duration_s = float(act.get("default_duration_s", 2.0))
        self.settle_tol_deg = float(act.get("settle_tol_deg", 2.0))
        self.sensors = [s["name"] if isinstance(s, dict) else str(s) for s in spec.get("sensors", [])]
        self._cam_map = {(s["name"] if isinstance(s, dict) else str(s)):
                         (s.get("camera", "") if isinstance(s, dict) else "") for s in spec.get("sensors", [])}
        self._lock = threading.Lock()
        self._last = "nothing yet"
        self._held = False          # emergency stop: the arm keeps its pose until released
        info = bus.spawn(ActuatorSpec(joint_names=self.joints, pd_mode=act.get("pd_mode", PD_POSITION_ACTUATOR)))
        self.joints = list(info.get("joint_names") or self.joints)
        self.limits: dict[str, tuple[float, float]] = {}
        for jn, lim in zip(self.joints, info.get("joint_limits") or []):
            self.limits[jn] = (float(lim[0]), float(lim[1]))
        if not self.joints:
            raise RuntimeError("the bus reported no joints for this arm")

    # -- helpers ----------------------------------------------------------------------------
    def _read(self):
        st = self.bus.read()
        return st, {jn: st.joint_pos[i] for i, jn in enumerate(self.joints)}

    def _clamp(self, jn: str, rad: float) -> float:
        lo, hi = self.limits.get(jn, (-math.inf, math.inf))
        return min(max(rad, lo), hi)

    def _gripper_pct(self, rad: float) -> float:
        lo, hi = self.limits.get(self.gripper, (0.0, 1.0))
        return 0.0 if hi == lo else 100.0 * (rad - lo) / (hi - lo)

    def _gripper_rad(self, pct: float) -> float:
        lo, hi = self.limits.get(self.gripper, (0.0, 1.0))
        return lo + (hi - lo) * min(max(pct, 0.0), 100.0) / 100.0

    def _interpolate(self, goal: dict[str, float], duration_s: float, _progress, should_abort) -> dict:
        """Move from current to goal (radians) over duration; write at rate; measure the end."""
        with self._lock:
            _, cur = self._read()
            start = dict(cur)
            n = max(1, int(duration_s * self.rate_hz))
            dt = 1.0 / self.rate_hz
            for k in range(1, n + 1):
                if should_abort():
                    break
                a = k / n
                targets = [start[jn] + a * (goal.get(jn, start[jn]) - start[jn]) for jn in self.joints]
                self.bus.write(BusCommand(targets=targets))
                if _progress and k % max(1, n // 10) == 0:
                    _progress(a, f"{int(a * 100)}%")
                time.sleep(dt)
            time.sleep(min(0.5, 10 * dt))          # settle
            _, end = self._read()
        errs = {jn: abs(end[jn] - goal[jn]) / DEG for jn in goal}
        worst = max(errs.values()) if errs else 0.0
        measured = {jn: round(end[jn] / DEG, 1) for jn in self.joints if jn != self.gripper}
        ok = worst <= self.settle_tol_deg * 3
        return {"ok": ok, "measured_deg": measured, "gripper_percent": round(self._gripper_pct(end[self.gripper]), 1)
                if self.gripper in end else None, "max_error_deg": round(worst, 1),
                "aborted": should_abort()}

    # -- BodyImpl ---------------------------------------------------------------------------
    def tools(self) -> list[dict]:
        arm_joints = [j for j in self.joints if j != self.gripper]
        lim_lines = "; ".join(f"{j}: {self.limits[j][0]/DEG:.0f}…{self.limits[j][1]/DEG:.0f}°"
                              for j in arm_joints if j in self.limits)
        return [
            {"name": "read_joints", "kind": KIND_READ,
             "description": "Read the current joint angles (degrees) and gripper opening (percent). "
                            "The observation already carries them; call this only to re-check after a move.",
             "parameters": {"type": "object", "properties": {}}},
            {"name": "move_joints", "kind": KIND_PRIMITIVE,
             "description": (f"Move any subset of joints to absolute angles in degrees, interpolated "
                             f"over duration_s, then report the measured angles. Joints: "
                             f"{', '.join(arm_joints)}. Limits: {lim_lines}. Targets outside the "
                             f"limits are clamped and reported. Use nudge for small relative moves."),
             "parameters": {"type": "object",
                            "properties": {"targets": {"type": "object", "description": "joint name → degrees",
                                                       "additionalProperties": {"type": "number"}},
                                           "duration_s": {"type": "number", "minimum": self.min_duration_s,
                                                          "maximum": self.max_duration_s,
                                                          "default": self.default_duration_s}},
                            "required": ["targets"]}},
            {"name": "nudge", "kind": KIND_PRIMITIVE,
             "description": (f"Move one joint by a relative amount in degrees (±{self.max_step_deg:g} max per "
                             f"call), then report the measured angle. Positive follows the joint's positive "
                             f"direction; the observation shows the current angle."),
             "parameters": {"type": "object",
                            "properties": {"joint": {"type": "string", "enum": arm_joints},
                                           "delta_deg": {"type": "number", "minimum": -self.max_step_deg,
                                                         "maximum": self.max_step_deg}},
                            "required": ["joint", "delta_deg"]}},
            {"name": "set_gripper", "kind": KIND_PRIMITIVE,
             "description": "Open or close the gripper: 0 = fully closed, 100 = fully open. "
                            "Reports the measured opening.",
             "parameters": {"type": "object",
                            "properties": {"percent": {"type": "number", "minimum": 0, "maximum": 100}},
                            "required": ["percent"]}},
        ]

    def observe(self):
        st, cur = self._read()
        state = {"joints_deg": {jn: round(cur[jn] / DEG, 1) for jn in self.joints if jn != self.gripper},
                 "gripper_percent": round(self._gripper_pct(cur[self.gripper]), 1) if self.gripper in cur else None,
                 "limits_deg": {jn: [round(lo / DEG), round(hi / DEG)] for jn, (lo, hi) in self.limits.items()
                                if jn != self.gripper},
                 "last_action": self._last, "t": round(st.t, 3)}
        images = []
        for name in self.sensors:
            try:
                data, mime = self.bus.sensor(self._cam_map.get(name) or name)
                if mime == "image/png":
                    images.append((name, data))
                elif mime == "image/jpeg":
                    from PIL import Image
                    import io
                    im = Image.open(io.BytesIO(data)).convert("RGB")
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    images.append((name, buf.getvalue()))
            except Exception as e:  # a missing camera is reported, never faked
                state.setdefault("sensor_errors", {})[name] = f"{type(e).__name__}: {e}"
        return state, images

    def invoke(self, name: str, *, _progress=None, **args) -> dict:
        if name == "read_joints":
            state, _ = self.observe()
            return {"ok": True, "message": f"joints {state['joints_deg']}, gripper {state['gripper_percent']}%",
                    "data": state}
        if name == "move_joints":
            targets = args.get("targets") or {}
            unknown = [j for j in targets if j not in self.joints or j == self.gripper]
            if unknown:
                return {"ok": False, "message": f"unknown joints: {unknown}; use {[j for j in self.joints if j != self.gripper]}"}
            if not targets:
                return {"ok": False, "message": "targets is empty"}
            dur = float(args.get("duration_s") or self.default_duration_s)
            dur = min(max(dur, self.min_duration_s), self.max_duration_s)
            goal = {j: self._clamp(j, float(v) * DEG) for j, v in targets.items()}
            clamped = [j for j, v in targets.items() if abs(goal[j] - float(v) * DEG) > 1e-6]
            res = self.runner.run(name, lambda p, a: self._interpolate(goal, dur, p, a), _progress)
            return self._report(name, res, extra=f" (clamped to limits: {clamped})" if clamped else "")
        if name == "nudge":
            j = args.get("joint")
            if j not in self.joints or j == self.gripper:
                return {"ok": False, "message": f"unknown joint {j!r}"}
            d = float(args.get("delta_deg", 0.0))
            d = min(max(d, -self.max_step_deg), self.max_step_deg)
            _, cur = self._read()
            goal = {j: self._clamp(j, cur[j] + d * DEG)}
            dur = min(max(abs(d) / 30.0, self.min_duration_s), self.max_duration_s)
            res = self.runner.run(name, lambda p, a: self._interpolate(goal, dur, p, a), _progress)
            return self._report(name, res)
        if name == "set_gripper":
            pct = float(args.get("percent", 0.0))
            goal = {self.gripper: self._gripper_rad(pct)}
            res = self.runner.run(name, lambda p, a: self._interpolate(goal, 1.0, p, a), _progress)
            return self._report(name, res)
        return {"ok": False, "message": f"no tool named {name!r}"}

    def _report(self, name: str, res: dict, extra: str = "") -> dict:
        if "measured_deg" not in res:
            return res
        msg = (f"{name} done: measured joints {res['measured_deg']}, gripper "
               f"{res['gripper_percent']}%, max error {res['max_error_deg']}°{extra}")
        if res.get("aborted"):
            msg = "stopped by the operator mid-move; " + msg
        self._last = msg
        return {"ok": bool(res.get("ok")) and not res.get("aborted"), "message": msg, "data": res}

    def hold(self, reason: str = "operator") -> dict:
        """Emergency stop that keeps the pose: abort the move, re-command the current joints so the
        servos stay powered where they are. (Cutting power instead would let the arm drop.)"""
        self.runner.stop.request()
        _, cur = self._read()
        self.bus.write(BusCommand(targets=[cur[jn] for jn in self.joints]))
        self._held = True
        self._last = f"holding the pose ({reason})"
        return {"ok": True, "message": self._last, "held": True, "reason": reason}

    def release(self) -> dict:
        self._held = False
        self._last = "released the hold"
        return {"ok": True, "message": self._last, "held": False, "reason": ""}

    @property
    def held(self) -> bool:
        return self._held

    def status(self) -> dict:
        st, cur = self._read()
        return {"joints_rad": cur, "t": st.t, "limits_rad": self.limits, "last_action": self._last,
                "held": self._held}

    def stream_jpeg(self) -> bytes | None:
        if not self.sensors:
            return None
        try:
            data, mime = self.bus.sensor(self._cam_map.get(self.sensors[0]) or self.sensors[0])
            return data if mime == "image/jpeg" else jpeg_from_png(data)
        except Exception:
            return None

    def sensor_names(self) -> list[str]:
        return list(self.sensors)

    def config_options(self) -> list[dict]:
        return []

    def set_option(self, key: str, value: str) -> dict:
        return {"ok": False, "message": f"no option named {key!r}"}

    def close(self) -> None:
        try:
            self.bus.close()
        except Exception:
            pass
