"""The humanoid family: a legged body driven by a released velocity-tracking gait policy.

The family is the robot's cerebellum. It runs the policy loop (System 1) at the contract's rate
over the motor bus — read joints/IMU, assemble the observation exactly as the training side did,
run the ONNX network, write joint targets — and offers three skills to the brain:
  move_forward(meters)   walk straight ahead, closed loop on the odometry, brake before obstacles
  turn_left(degrees)     turn on the spot, closed loop on the yaw
  turn_right(degrees)
A skill ends when the measured quantity reaches the target, when the body stalls (a wall), when
the front cone gets too close (brake, forward walking only), when the body falls, on timeout or
on stop. What comes back is MEASURED (displacement, turn), never the command echoed.

Everything about the policy comes from its release directory (body.yaml `skills.*.policy`):
  policy.onnx      the network (CPU inference)
  contract.json    joint order, default pose, observation terms/scales/history, action scale,
                   timing, per-actuator-group kp/kd/effort limits — exported from the training env
  release.yaml     pd_mode, command ranges, the linear-command deadband, turn_vx
⛔ None of that is ever guessed in code: a hand-written joint order fails silently and the robot
   is on the floor in a second (the G1 sim2sim lesson).
Everything measured on the bus is in SIM time (or the robot's clock on hardware), never the wall.

The brain-facing observation carries sensor-grade facts only: clearances, the front cone, whether
the body fell, a heading relative to where it started, the last action's outcome, the head camera.
⛔ No coordinates, no room names — that is the world's /status, for humans.
"""
from __future__ import annotations

import io
import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ...nerve.body import KIND_SKILL
from ...nerve.world import (PD_EXPLICIT, PD_IMPLICIT, PD_POSITION_ACTUATOR, ActuatorSpec, BusCommand,
                            BusState, MotorBus)
from ..skills import SkillRunner

# ---- knobs: body.yaml `locomotion:` overrides any of these ---------------------------------------------
DEFAULT_LOCOMOTION: dict = {
    "walk_speed": 0.6,             # vx (m/s) for move_forward
    "turn_rate": 0.8,              # wz (rad/s) for turns
    "max_move_m": 3.0,
    "max_turn_deg": 180.0,
    "default_move_m": 1.0,
    "default_turn_deg": 45.0,
    "settle_s": 0.6,               # stand still this long (sim s) after a skill, so the next frame is sharp
    "lidar_range_m": 8.0,          # a ray that hits nothing reports this
    "brake_margin_m": 0.20,        # brake line = measured front extent + this margin
    "brake_cone_deg": 40.0,        # width of the cone watched while walking forward
    "brake_rays": 5,               # rays across that cone; the nearest one counts
    "stall_timeout_s": 1.5,        # no progress for this long (sim s) = stuck against something
    "stall_eps_m": 0.02,           # progress smaller than this does not count (m or rad)
    "fall_tilt_rad": 0.8,          # torso tilt beyond this = fallen (matches the training termination)
    "fall_height_m": 0.18,         # base lower than this = lying on the floor
    "time_factor": 2.5,            # skill time budget = ideal duration × this …
    "time_extra_s": 1.5,           # … + this (start-up / wind-down)
    "poll_s": 0.02,                # closed-loop polling period (WALL: it is just a cadence)
    "sim_dead_wall_s": 5.0,        # sim clock frozen for this long (wall) = the world is gone
    "brake_zero_move_m": 0.05,     # braked having moved less than this = "could not start at all"
    "turn_report_move_m": 0.15,    # a turn that drifted more than this says so
    "stuck_min_ratio": 0.35,       # reached less than this fraction of the target = blocked
    "progress_start": 0.1,         # first progress tick, so the operator sees it start
    "hold_settle_s": 1.5,          # emergency stop: let the gait come to a stand for at most this (sim s)
                                   # before latching the joints — freezing legs mid-stride topples a biped
                                   # (measured 2026-09-06: the g1-29dof-turn gait needs ~1.2 s to stop)
    "hold_still_rad_s": 0.3,       # every joint slower than this = standing still, latch now
                                   # (a standing G1 measures 0.02 rad/s; mid-stride 3–8 rad/s)
}

# the eight bearings the brain gets clearances for (name, degrees CCW from straight ahead)
CLEARANCE_BEARINGS = (("front", 0), ("front_left", 45), ("left", 90), ("back_left", 135),
                      ("back", 180), ("back_right", 225), ("right", 270), ("front_right", 315))
# observation terms the deployer knows how to assemble, in the training-side default order
TERM_ORDER = ("base_ang_vel", "projected_gravity", "velocity_commands",
              "joint_pos_rel", "joint_vel_rel", "last_action")
PD_MODE_FROM_RELEASE = "release"

REASON_REACHED, REASON_BRAKED, REASON_STALLED = "reached", "braked", "stalled"
REASON_FALLEN, REASON_BUDGET, REASON_STOPPED, REASON_SIM_DEAD = "fallen", "budget", "stopped", "sim_dead"
REASON_HELD = "held"
# why the body is holding its pose: the operator pressed the emergency stop, or it fell
HOLD_OPERATOR, HOLD_FALLEN = "operator", "fallen"


def build(spec: dict, bus: MotorBus, runner: SkillRunner):
    return HumanoidBody(spec, bus, runner)


@dataclass
class HumanoidActuatorSpec(ActuatorSpec):
    """ActuatorSpec plus what the world should know about this body (spawn height, chase camera,
    fall thresholds). The ZMQ client forwards only the base fields today; the world also reads
    these from the registry, so nothing is lost on the way."""
    extra: dict = field(default_factory=dict)


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def _rot(quat_wxyz) -> np.ndarray:
    w, x, y, z = (float(v) for v in quat_wxyz)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _load_yaml(path: str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---- the policy release -----------------------------------------------------------------------------------
class PolicyRelease:
    """policy.onnx + contract.json + release.yaml, read once, nothing invented."""

    def __init__(self, path: str) -> None:
        self.dir = path
        for fn in ("policy.onnx", "contract.json", "release.yaml"):
            if not os.path.isfile(os.path.join(path, fn)):
                raise FileNotFoundError(f"policy release {path} lacks {fn}")
        with open(os.path.join(path, "contract.json"), encoding="utf-8") as f:
            c = json.load(f)
        rel = _load_yaml(os.path.join(path, "release.yaml"))
        self.name = str(rel.get("policy_id") or os.path.basename(path))
        self.joint_names: list[str] = list(c["joint_names"])
        self.default_pos = np.asarray(c["default_joint_pos"], np.float64)
        self.scales: dict = dict(c["term_scales"])
        self.action_scale = float(c["action"]["scale"])
        self.policy_dt = float(c["timing"]["policy_dt_s"])
        self.history = int(c.get("history_length", 1) or 1)
        terms = [t["term"] if isinstance(t, dict) else str(t) for t in (c.get("obs_terms") or [])]
        self.term_order: tuple[str, ...] = tuple(terms) or TERM_ORDER
        unknown = [t for t in self.term_order if t not in TERM_ORDER]
        if unknown:
            raise ValueError(f"contract observation terms {unknown} are not ones this family assembles")
        self.obs_dim = int(c.get("obs_dim", 0) or 0)
        gains = self._gains(c)
        missing = [n for n in self.joint_names if n not in gains]
        if missing:
            raise ValueError(f"contract has no gains for {missing}")
        self.kp = [float(gains[n]["kp"]) for n in self.joint_names]
        self.kd = [float(gains[n]["kd"]) for n in self.joint_names]
        self.torque_limit = [float(gains[n]["effort_limit"]) for n in self.joint_names]
        self.pd_mode = str(rel.get("pd_mode") or "")
        if self.pd_mode not in (PD_IMPLICIT, PD_EXPLICIT, PD_POSITION_ACTUATOR):
            raise ValueError(f"release.yaml pd_mode must be {PD_IMPLICIT}/{PD_EXPLICIT}/"
                             f"{PD_POSITION_ACTUATOR}, got {self.pd_mode!r}")
        rng = rel.get("command_ranges") or {}
        self.cmd_ranges = {k: (float(v[0]), float(v[1])) for k, v in rng.items()}
        self.lin_deadband = float(rel.get("lin_cmd_deadband", 0.0) or 0.0)
        self.turn_vx = float(rel.get("turn_vx", 0.0) or 0.0)
        import onnxruntime as ort
        self._sess = ort.InferenceSession(os.path.join(path, "policy.onnx"), providers=["CPUExecutionProvider"])
        self._in = self._sess.get_inputs()[0].name

    @staticmethod
    def _gains(c: dict) -> dict:
        if "gains" in c:
            return c["gains"]
        out: dict = {}
        for grp in (c.get("actuators") or {}).values():
            names = grp["joint_names"]
            for i, n in enumerate(names):
                def pick(arr, i=i):
                    if not arr:
                        return 0.0
                    return float(arr[i]) if i < len(arr) else float(arr[0])
                out[n] = {"kp": pick(grp.get("stiffness", [])), "kd": pick(grp.get("damping", [])),
                          "effort_limit": pick(grp.get("effort_limit", [])) or math.inf}
        return out

    def act(self, obs: np.ndarray) -> np.ndarray:
        return self._sess.run(None, {self._in: obs[None, :].astype(np.float32)})[0][0].astype(np.float64)

    def clamp_command(self, vx: float, vy: float, wz: float) -> tuple[float, float, float]:
        """Command ranges from release.yaml; the linear deadband snaps (0, deadband) to 0 or
        deadband — the policy never saw commands in that gap."""
        def snap(v: float) -> float:
            db = self.lin_deadband
            if db <= 0 or v == 0.0:
                return v
            return 0.0 if abs(v) < db / 2 else math.copysign(max(abs(v), db), v)

        def clip(k: str, v: float) -> float:
            lo, hi = self.cmd_ranges.get(k, (-math.inf, math.inf))
            return min(max(v, lo), hi)
        return clip("vx", snap(vx)), clip("vy", snap(vy)), clip("wz", wz)


# ---- the body -----------------------------------------------------------------------------------------------------
class HumanoidBody:
    family = "humanoid"

    def __init__(self, spec: dict, bus: MotorBus, runner: SkillRunner) -> None:
        self.name = spec["name"]
        self.version = str(spec.get("version", "0"))
        self.spec = spec
        self.bus = bus
        self.runner = runner
        self.loco = dict(DEFAULT_LOCOMOTION)
        self.loco.update(self._locomotion_knobs(spec))
        self.sensors = [s["name"] if isinstance(s, dict) else str(s) for s in spec.get("sensors", [])]
        self._cam_map = {(s["name"] if isinstance(s, dict) else str(s)):
                         (s.get("camera", "") if isinstance(s, dict) else "") for s in spec.get("sensors", [])}

        skills = spec.get("skills") or {}
        dirs = {str((v or {}).get("policy", "")) for v in skills.values()}
        dirs.discard("")
        if len(dirs) != 1:
            raise ValueError(f"the humanoid skills must share one policy release; body.yaml names {sorted(dirs)}")
        self.policy = PolicyRelease(dirs.pop())
        act = spec.get("actuators") or {}
        if act.get("source", "contract") != "contract":
            raise ValueError(f"actuators.source {act.get('source')!r}: only 'contract' is supported")
        mode = str(act.get("pd_mode") or PD_MODE_FROM_RELEASE)
        pd_mode = self.policy.pd_mode if mode == PD_MODE_FROM_RELEASE else mode
        extra = {k: act[k] for k in ("start_height", "chase_body", "chase_back_m", "chase_up_m") if k in act}
        extra.update({"fall_tilt_rad": float(self.loco["fall_tilt_rad"]),
                      "fall_height_m": float(self.loco["fall_height_m"])})
        info = bus.spawn(HumanoidActuatorSpec(joint_names=self.policy.joint_names, pd_mode=pd_mode,
                                              kp=self.policy.kp, kd=self.policy.kd,
                                              torque_limit=self.policy.torque_limit,
                                              default_pos=self.policy.default_pos.tolist(), extra=extra))
        if list(info.get("joint_names") or []) != self.policy.joint_names:
            raise RuntimeError("the bus did not confirm the policy's joint order")
        self.pd_mode = pd_mode
        self.front_extent_m = float(info.get("front_extent_m", 0.0) or 0.0)
        self.brake_stop_m = self.front_extent_m + float(self.loco["brake_margin_m"])
        self.has_free_base = bool(info.get("has_free_base", True))

        self._cmd_lock = threading.Lock()
        self._cmd = np.zeros(3)                # vx vy wz, as sent to the policy
        self._state: BusState | None = None     # last bus read, for observe/status
        self._yaw0: float | None = None         # heading reference: where the body first stood
        self._last = "nothing yet"
        self._sim_dead = False
        self._policy_error = ""
        # Hold = emergency stop that keeps the pose: the policy loop stops thinking and the PD
        # keeps every joint where it is (a real robot in damping mode; a powered-off servo robot
        # would go limp instead). Entered by the operator or automatically on a fall — a gait
        # policy fed a fallen body's observation only thrashes, which is what the twitching was.
        self._hold_lock = threading.Lock()
        self._held = False
        self._hold_reason = ""
        self._hold_targets: list[float] = []
        self._hold_pending: tuple[str, float] | None = None      # (reason, sim deadline) while coming to a stand
        self._last_targets: list[float] = []                     # what the PD is tracking right now
        self._running = True
        self._thread = threading.Thread(target=self._policy_loop, name="nerv-humanoid-policy", daemon=True)
        self._thread.start()

    @staticmethod
    def _locomotion_knobs(spec: dict) -> dict:
        """`locomotion:` from the spec dict, or from body.yaml on disk when the registry schema
        dropped it (BodySpec has no such field yet)."""
        loco = spec.get("locomotion")
        if loco is None and spec.get("dir"):
            try:
                loco = _load_yaml(os.path.join(spec["dir"], "body.yaml")).get("locomotion")
            except Exception:
                loco = None
        return dict(loco or {})

    # ---- System 1: the policy loop -------------------------------------------------------------------------
    def _set_command(self, vx: float, vy: float, wz: float) -> None:
        with self._cmd_lock:
            self._cmd = np.array(self.policy.clamp_command(vx, vy, wz), np.float64)

    def _observation(self, st: BusState, hist: dict | None, prev_action: np.ndarray):
        p = self.policy
        R = _rot(st.imu_quat) if len(st.imu_quat) == 4 else np.eye(3)
        gyro = np.asarray(st.imu_gyro if len(st.imu_gyro) == 3 else [0.0, 0.0, 0.0], np.float64)
        grav_b = R.T @ np.array([0.0, 0.0, -1.0])
        with self._cmd_lock:
            cmd = self._cmd.copy()
        s = p.scales
        frame = {
            "base_ang_vel": gyro * s["base_ang_vel"],
            "projected_gravity": grav_b * s["projected_gravity"],
            "velocity_commands": cmd * s["velocity_commands"],
            "joint_pos_rel": (np.asarray(st.joint_pos, np.float64) - p.default_pos) * s["joint_pos_rel"],
            "joint_vel_rel": np.asarray(st.joint_vel, np.float64) * s["joint_vel_rel"],
            "last_action": prev_action * s["last_action"],
        }
        frame = {k: np.asarray(v, np.float64).ravel() for k, v in frame.items()}
        if p.history <= 1:
            return np.concatenate([frame[k] for k in p.term_order]), None
        # per-term history blocks (oldest → newest), then the blocks back to back — the training
        # layout; a per-frame layout has the same size and silently wrong values
        if hist is None:
            hist = {k: deque([frame[k]] * p.history, maxlen=p.history) for k in p.term_order}
        else:
            for k in p.term_order:
                hist[k].append(frame[k])
        return np.concatenate([np.concatenate(list(hist[k])) for k in p.term_order]), hist

    def _policy_loop(self) -> None:
        p = self.policy
        hist = None
        prev_action = np.zeros(len(p.joint_names))
        last_t: float | None = None
        last_advance_wall = time.perf_counter()
        dead_wall = float(self.loco["sim_dead_wall_s"])
        while self._running:
            try:
                st = self.bus.read()
            except Exception as e:
                self._policy_error = f"{type(e).__name__}: {e}"
                time.sleep(p.policy_dt)
                continue
            if last_t is not None and st.t < last_t:          # the world was reset: start over
                hist, prev_action, last_t = None, np.zeros(len(p.joint_names)), None
                self._yaw0 = None
                self._release(after_reset=True)                # back at the spawn pose: nothing to hold
            if last_t is not None:
                deadline = last_t + p.policy_dt
                if st.t < deadline - 0.25 * p.policy_dt:
                    if time.perf_counter() - last_advance_wall > dead_wall:
                        self._sim_dead = True
                    time.sleep(min(p.policy_dt, max(0.0005, 0.8 * (deadline - st.t))))
                    continue
            last_t = st.t
            last_advance_wall = time.perf_counter()
            self._sim_dead = False
            self._state = st
            if self._yaw0 is None:
                self._yaw0 = float(st.odom_yaw)
            if not self._held and self._fallen(st):
                self._hold(HOLD_FALLEN, st)
            pend = self._hold_pending
            if pend is not None and not self._held:                # emergency stop: latch once still
                reason, deadline = pend
                fastest = max((abs(float(v)) for v in st.joint_vel), default=0.0)
                if fastest < float(self.loco["hold_still_rad_s"]) or st.t >= deadline:
                    self._hold(reason, st)
            with self._hold_lock:
                hold = list(self._hold_targets) if self._held else None
            if hold is not None:                                # holding: keep the pose, do not think
                try:
                    self.bus.write(BusCommand(targets=hold))
                except Exception as e:
                    self._policy_error = f"{type(e).__name__}: {e}"
                hist, prev_action = None, np.zeros(len(p.joint_names))   # a fresh start on release
                continue
            try:
                obs, hist = self._observation(st, hist, prev_action)
                if p.obs_dim and obs.shape[0] != p.obs_dim:
                    raise ValueError(f"assembled {obs.shape[0]} observation values, contract says {p.obs_dim}")
                action = p.act(obs)
                prev_action = action
                targets = p.default_pos + action * p.action_scale
                self.bus.write(BusCommand(targets=targets.tolist()))
                self._last_targets = targets.tolist()
                self._policy_error = ""
            except Exception as e:
                self._policy_error = f"{type(e).__name__}: {e}"
                time.sleep(p.policy_dt)

    # ---- measurements from the bus ------------------------------------------------------------------------
    def _tilt(self, st: BusState) -> float:
        if len(st.imu_quat) != 4:
            return 0.0
        return math.acos(max(-1.0, min(1.0, float(_rot(st.imu_quat)[2, 2]))))

    def _fallen(self, st: BusState) -> bool:
        if not self.has_free_base:
            return False
        return self._tilt(st) > float(self.loco["fall_tilt_rad"]) or st.base_height < float(self.loco["fall_height_m"])

    # ---- hold (emergency stop that keeps the pose) --------------------------------------------------------
    def _hold(self, reason: str, st: BusState | None = None) -> dict:
        """Latch the current joint positions as the targets and stop the policy. Idempotent."""
        self.runner.stop.request()                              # any running skill ends on its next tick
        self._set_command(0.0, 0.0, 0.0)
        with self._hold_lock:
            self._hold_pending = None
            if self._held:
                return {"ok": True, "message": f"already holding the pose ({self._hold_reason})",
                        "held": True, "reason": self._hold_reason}
            if st is None:
                st = self._state or self.bus.read()
            # Freeze the command stream, not the measured angles: the PD is already at equilibrium
            # for the last targets (they carry the gravity offset the policy learned), so nothing
            # sags or jumps. Only a body that never got a command latches its measured pose.
            self._hold_targets = [float(v) for v in (self._last_targets or st.joint_pos)]
            self._held, self._hold_reason = True, reason
            targets = list(self._hold_targets)
        try:
            self.bus.write(BusCommand(targets=targets))           # do not wait for the loop's next tick
        except Exception as e:
            self._policy_error = f"{type(e).__name__}: {e}"
        self._last = "holding the pose" + (" after a fall" if reason == HOLD_FALLEN else " (emergency stop)")
        return {"ok": True, "message": self._last, "held": True, "reason": reason}

    def _release(self, after_reset: bool = False) -> dict:
        with self._hold_lock:
            self._hold_pending = None
            if not self._held:
                return {"ok": True, "message": "not holding", "held": False, "reason": ""}
            was = self._hold_reason
            self._held, self._hold_reason, self._hold_targets = False, "", []
        self._last = "back at the spawn pose" if after_reset else f"released the hold ({was}); standing"
        return {"ok": True, "message": self._last, "held": False, "reason": ""}

    def hold(self, reason: str = HOLD_OPERATOR) -> dict:
        """Emergency stop that keeps the pose. A walking biped is first told to stand (zero command,
        the gait balances itself) and latched as soon as it is still; a fallen or idle body is
        latched at once. Returns when the joints are latched."""
        reason = reason or HOLD_OPERATOR
        st = self._state
        if self._held or st is None or self._fallen(st) or self._sim_dead:
            return self._hold(reason, st)
        settle = float(self.loco["hold_settle_s"])
        self.runner.stop.request()
        self._set_command(0.0, 0.0, 0.0)
        with self._hold_lock:
            self._hold_pending = (reason, st.t + settle)
        wall_limit = time.perf_counter() + settle + float(self.loco["sim_dead_wall_s"])
        while not self._held and time.perf_counter() < wall_limit:
            time.sleep(float(self.loco["poll_s"]))
        if not self._held:                                      # the loop never got there: latch anyway
            return self._hold(reason, self._state)
        return {"ok": True, "message": self._last, "held": True, "reason": self._hold_reason}

    def release(self) -> dict:
        st = self._state or self.bus.read()
        if self._fallen(st):
            return {"ok": False, "held": self._held, "reason": self._hold_reason,
                    "message": ("the body is down; releasing would only make it thrash. "
                                "Reset the world (simulation) or stand it up first (hardware)")}
        return self._release()

    @property
    def held(self) -> bool:
        return self._held

    def _front_cone(self) -> float:
        n = max(1, int(self.loco["brake_rays"]))
        half = float(self.loco["brake_cone_deg"]) / 2.0
        angles = [0.0] if n == 1 else [-half + 2 * half * i / (n - 1) for i in range(n)]
        return min(self.bus.rays(angles, float(self.loco["lidar_range_m"])))

    def _clearances(self) -> dict[str, float]:
        r = self.bus.rays([float(d) for _n, d in CLEARANCE_BEARINGS], float(self.loco["lidar_range_m"]))
        return {name: round(v, 2) for (name, _d), v in zip(CLEARANCE_BEARINGS, r)}

    def _sleep_sim(self, seconds: float, should_abort) -> None:
        if seconds <= 0:
            return
        st = self.bus.read()
        end = st.t + seconds
        last_t, last_wall = st.t, time.perf_counter()
        while True:
            st = self.bus.read()
            if st.t >= end or should_abort():
                return
            if st.t > last_t:
                last_t, last_wall = st.t, time.perf_counter()
            elif time.perf_counter() - last_wall > float(self.loco["sim_dead_wall_s"]):
                return
            time.sleep(float(self.loco["poll_s"]))

    def _drive_until(self, vx: float, vy: float, wz: float, budget_s: float, kind: str, target: float,
                     progress, should_abort) -> dict:
        """Hold a velocity command until the MEASURED quantity reaches the target, or stall /
        brake / fall / budget / stop. Closed loop on the odometry like any real navigation stack:
        the gait still walks by itself; we only decide when to stop commanding."""
        L = self.loco
        st = self.bus.read()
        x0, y0 = (st.odom_xy + [0.0, 0.0])[:2]
        prev_yaw, acc_yaw = st.odom_yaw, 0.0
        best, t_stall = 0.0, st.t
        t_end = st.t + max(0.0, budget_s)
        reason, braked_at = REASON_BUDGET, None
        wd_sim, wd_wall = st.t, time.perf_counter()
        self._set_command(vx, vy, wz)
        try:
            while True:
                st = self.bus.read()
                if st.t >= t_end:
                    break
                if st.t > wd_sim:
                    wd_sim, wd_wall = st.t, time.perf_counter()
                elif time.perf_counter() - wd_wall > float(L["sim_dead_wall_s"]):
                    reason = REASON_SIM_DEAD
                    break
                if should_abort():
                    reason = REASON_STOPPED
                    break
                if self._fallen(st):
                    reason = REASON_FALLEN
                    break
                if kind == "dist":
                    front = self._front_cone()
                    if front < self.brake_stop_m:       # stop, never steer around: that is the brain's call
                        braked_at, reason = front, REASON_BRAKED
                        break
                x, y = (st.odom_xy + [0.0, 0.0])[:2]
                acc_yaw += _wrap(st.odom_yaw - prev_yaw)
                prev_yaw = st.odom_yaw
                prog = math.hypot(x - x0, y - y0) if kind == "dist" else abs(acc_yaw)
                if prog >= target:
                    reason = REASON_REACHED
                    break
                if prog > best + float(L["stall_eps_m"]):
                    best, t_stall = prog, st.t
                elif st.t - t_stall > float(L["stall_timeout_s"]):
                    reason = REASON_STALLED
                    break
                if progress and target > 0:
                    progress(min(0.99, max(float(L["progress_start"]), prog / target)),
                             f"{prog:.2f}/{target:.2f} {'m' if kind == 'dist' else 'rad'}")
                time.sleep(float(L["poll_s"]))
        finally:
            self._set_command(0.0, 0.0, 0.0)
        self._sleep_sim(float(L["settle_s"]), should_abort)
        st = self.bus.read()
        x1, y1 = (st.odom_xy + [0.0, 0.0])[:2]
        acc_yaw += _wrap(st.odom_yaw - prev_yaw)
        return {"moved_m": round(math.hypot(x1 - x0, y1 - y0), 3), "turned_deg": round(math.degrees(acc_yaw), 1),
                "fallen": self._fallen(st), "reason": reason,
                "front_m": round(braked_at, 2) if braked_at is not None else None,
                "braked": reason == REASON_BRAKED, "stalled": reason == REASON_STALLED,
                "sim_time": round(st.t, 3)}

    # ---- skills -----------------------------------------------------------------------------------------------
    def _move_forward(self, meters: float, progress, should_abort) -> dict:
        L = self.loco
        speed = float(L["walk_speed"])
        budget = meters / max(1e-6, speed) * float(L["time_factor"]) + float(L["time_extra_s"])
        progress(float(L["progress_start"]), f"walking forward {meters:g} m")
        r = self._drive_until(speed, 0.0, 0.0, budget, "dist", meters, progress, should_abort)
        moved = r["moved_m"]
        r["target_m"] = meters
        if r["fallen"]:
            return {"ok": False, "message": f"fell while walking forward (moved {moved:.2f} m)", "data": r}
        if r["reason"] == REASON_STOPPED:
            return {"ok": False, "message": f"stopped by the operator after {moved:.2f} m", "data": r}
        if r["reason"] == REASON_SIM_DEAD:
            return {"ok": False, "message": f"the world stopped answering after {moved:.2f} m", "data": r}
        if r["reason"] == REASON_BRAKED:
            if moved < float(L["brake_zero_move_m"]):
                return {"ok": True, "message": (f"did not move: only {r['front_m']:.2f} m ahead, already at "
                                                f"the safety distance — this heading is blocked"), "data": r}
            return {"ok": True, "message": (f"walked {moved:.2f} m then stopped: only {r['front_m']:.2f} m "
                                            f"ahead, braked at the safety distance"), "data": r}
        if r["reason"] == REASON_STALLED or moved < meters * float(L["stuck_min_ratio"]):
            return {"ok": True, "message": f"walked only {moved:.2f} m and got stuck — blocked by a wall "
                                           f"or furniture", "data": r}
        return {"ok": True, "message": f"walked forward {moved:.2f} m", "data": r}

    def _turn(self, degrees: float, sign: int, progress, should_abort) -> dict:
        L = self.loco
        rate = float(L["turn_rate"])
        rad = math.radians(degrees)
        budget = rad / max(1e-6, rate) * float(L["time_factor"]) + float(L["time_extra_s"])
        side = "left" if sign > 0 else "right"
        progress(float(L["progress_start"]), f"turning {side} {degrees:g}°")
        r = self._drive_until(self.policy.turn_vx, 0.0, sign * rate, budget, "yaw", rad, progress, should_abort)
        turned = abs(r["turned_deg"])
        r["target_deg"] = degrees
        if r["fallen"]:
            return {"ok": False, "message": f"fell while turning {side} (turned {turned:.0f}°)", "data": r}
        if r["reason"] == REASON_STOPPED:
            return {"ok": False, "message": f"stopped by the operator after turning {turned:.0f}°", "data": r}
        if r["reason"] == REASON_SIM_DEAD:
            return {"ok": False, "message": f"the world stopped answering after {turned:.0f}°", "data": r}
        msg = f"turned {side} {turned:.0f}°"
        if r["moved_m"] >= float(L["turn_report_move_m"]):
            msg += f", drifting {r['moved_m']:.2f} m"
        return {"ok": True, "message": msg, "data": r}

    # ---- BodyImpl ------------------------------------------------------------------------------------------------
    def tools(self) -> list[dict]:
        L = self.loco
        return [
            {"name": "move_forward", "kind": KIND_SKILL,
             "description": (f"Walk straight ahead a distance in metres (at most {L['max_move_m']:g} per call). "
                             f"The body walks with its real gait, so the distance covered is measured, not "
                             f"assumed; it brakes on its own when the front cone gets too close and stops "
                             f"if it runs into a wall or furniture. Either way you are told how far it "
                             f"went and how much room is left ahead. It never steers around obstacles: "
                             f"where to go, and what to do when blocked, is your decision."),
             "parameters": {"type": "object",
                            "properties": {"meters": {"type": "number", "minimum": 0,
                                                      "maximum": float(L["max_move_m"]),
                                                      "default": float(L["default_move_m"]),
                                                      "description": "distance to walk forward, metres"}},
                            "required": ["meters"]}},
            {"name": "turn_left", "kind": KIND_SKILL,
             "description": (f"Turn left (counter-clockwise) on the spot by an angle in degrees (at most "
                             f"{L['max_turn_deg']:g} per call). The turn is measured; it stands still "
                             f"afterwards so the next image is sharp."),
             "parameters": {"type": "object",
                            "properties": {"degrees": {"type": "number", "minimum": 0,
                                                       "maximum": float(L["max_turn_deg"]),
                                                       "default": float(L["default_turn_deg"]),
                                                       "description": "angle to turn left, degrees"}},
                            "required": ["degrees"]}},
            {"name": "turn_right", "kind": KIND_SKILL,
             "description": (f"Turn right (clockwise) on the spot by an angle in degrees (at most "
                             f"{L['max_turn_deg']:g} per call). The turn is measured; it stands still "
                             f"afterwards so the next image is sharp."),
             "parameters": {"type": "object",
                            "properties": {"degrees": {"type": "number", "minimum": 0,
                                                       "maximum": float(L["max_turn_deg"]),
                                                       "default": float(L["default_turn_deg"]),
                                                       "description": "angle to turn right, degrees"}},
                            "required": ["degrees"]}},
        ]

    def observe(self):
        st = self.bus.read()
        heading = math.degrees(_wrap(st.odom_yaw - (self._yaw0 if self._yaw0 is not None else st.odom_yaw)))
        state = {"clearance_m": self._clearances(), "front_cone_m": round(self._front_cone(), 2),
                 "fallen": self._fallen(st), "heading_deg": round(heading, 1),
                 "held": self._held, "hold_reason": self._hold_reason,
                 "last_action": self._last, "t": round(st.t, 3)}
        if self._policy_error:
            state["policy_error"] = self._policy_error
        images = []
        for name in self.sensors:
            try:
                data, mime = self.bus.sensor(self._cam_map.get(name) or name)
                if mime == "image/png":
                    images.append((name, data))
                elif mime == "image/jpeg":
                    from PIL import Image
                    im = Image.open(io.BytesIO(data)).convert("RGB")
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    images.append((name, buf.getvalue()))
            except Exception as e:      # a missing camera is reported, never faked
                state.setdefault("sensor_errors", {})[name] = f"{type(e).__name__}: {e}"
        return state, images

    def invoke(self, name: str, *, _progress=None, **args) -> dict:
        L = self.loco
        if self._held:
            why = "it fell" if self._hold_reason == HOLD_FALLEN else "the operator pressed the emergency stop"
            return {"ok": False, "message": (f"the body is holding its pose because {why}; it will not move "
                                             f"until the operator releases it"
                                             + (" (reset the world first)" if self._hold_reason == HOLD_FALLEN else "")),
                    "data": {"reason": REASON_HELD, "held": True, "hold_reason": self._hold_reason}}
        if name == "move_forward":
            meters = float(args.get("meters", L["default_move_m"]))
            if meters <= 0:
                return {"ok": False, "message": "meters must be positive"}
            capped = min(meters, float(L["max_move_m"]))
            note = "" if capped == meters else f" (you asked for {meters:g} m; at most {L['max_move_m']:g} m per call)"
            res = self.runner.run(name, lambda p, a: self._move_forward(capped, p, a), _progress)
        elif name in ("turn_left", "turn_right"):
            degrees = float(args.get("degrees", L["default_turn_deg"]))
            if degrees <= 0:
                return {"ok": False, "message": "degrees must be positive"}
            capped = min(degrees, float(L["max_turn_deg"]))
            note = "" if capped == degrees else f" (you asked for {degrees:g}°; at most {L['max_turn_deg']:g}° per call)"
            sign = 1 if name == "turn_left" else -1
            res = self.runner.run(name, lambda p, a: self._turn(capped, sign, p, a), _progress)
        else:
            return {"ok": False, "message": f"no tool named {name!r}"}
        if note and "message" in res:
            res["message"] += note
        self._last = res.get("message", "")
        return res

    def status(self) -> dict:
        """The operator's view (HTTP /status on the body node): bus state, command, policy health."""
        st = self._state
        with self._cmd_lock:
            cmd = self._cmd.tolist()
        return {"policy": self.policy.name, "pd_mode": self.pd_mode, "command": cmd,
                "running_skill": self.runner.running, "last_action": self._last,
                "held": self._held, "hold_reason": self._hold_reason,
                "hold_pending": self._hold_pending is not None,
                "sim_dead": self._sim_dead, "policy_error": self._policy_error,
                "front_extent_m": round(self.front_extent_m, 3), "brake_stop_m": round(self.brake_stop_m, 3),
                "state": None if st is None else {"t": round(st.t, 3), "odom_xy": st.odom_xy,
                                                  "odom_yaw_deg": round(math.degrees(st.odom_yaw), 1),
                                                  "base_height": round(st.base_height, 3),
                                                  "tilt_deg": round(math.degrees(self._tilt(st)), 1),
                                                  "fallen": self._fallen(st)}}

    def stream_jpeg(self) -> bytes | None:
        if not self.sensors:
            return None
        try:
            data, mime = self.bus.sensor(self._cam_map.get(self.sensors[0]) or self.sensors[0])
            if mime == "image/jpeg":
                return data
            from ..node import jpeg_from_png
            return jpeg_from_png(data)
        except Exception:
            return None

    def sensor_names(self) -> list[str]:
        return list(self.sensors)

    def config_options(self) -> list[dict]:
        return []

    def set_option(self, key: str, value: str) -> dict:
        return {"ok": False, "message": f"no option named {key!r}"}

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self.bus.close()
        except Exception:
            pass
