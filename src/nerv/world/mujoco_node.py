"""NERV/World, simulation endpoint: a MuJoCo arena with motor firmware, cameras and a rangefinder.

One process = one arena (an MJCF scene, usually built by alice-house) hosting one body.
  * Physics runs on a background thread at ``physics.dt`` (world.yaml), throttled to
    ``physics.realtime_factor`` of wall time. Everything measured is SIM time.
  * The motor bus (ZMQ REP, nerve/wire.py JSON) is what the body node talks to at policy rate:
    spawn / read / write / reset / sensors / sensor / rays / epoch / close.
  * "Motor firmware": at spawn the body says how its joints are driven (nerve.world.PD_*):
      implicit          kd goes into dof_damping, we apply tau = kp*(q*-q)      (Unitree G1 policies)
      explicit          damping zeroed, we apply   tau = kp*(q*-q) - kd*qd      (Go2 policies)
      position_actuator targets go to the MJCF position actuators              (SO-101 arm)
    ⛔ implicit vs explicit is the single easiest thing to get wrong here: writing kd as an
    external torque for a policy trained with implicit PD makes the joint velocities ring from
    the first step and the robot is on the floor within a second (2026-07-24, a full day lost).
  * Control plane over HTTP (FastAPI): /health, /status (GOD'S EYE — humans only, never the
    brain), /sensors, /sensors/<name>, POST /reset, /stream (chase camera), /stream/<camera>, /.

Nothing here imports the platform or the body: the world is its own process and reads its own
knobs from world.yaml / body.yaml (through the registry) or from the command line.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import math
import os
import queue
import sys
import threading
import time
import uuid
from typing import Any, Callable

os.environ.setdefault("MUJOCO_GL", "egl")     # headless rendering; a display is never assumed
import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from ..nerve.world import (OP_CLOSE, OP_EPOCH, OP_RAYS, OP_READ, OP_RESET, OP_SENSOR,  # noqa: E402
                           OP_SENSORS, OP_SPAWN, OP_WRITE, PD_EXPLICIT, PD_IMPLICIT,
                           PD_POSITION_ACTUATOR)
from .bus_server import BusServer  # noqa: E402

# ---- named defaults: every one of these can be overridden from world.yaml `physics:` ------------
DEFAULT_PHYSICS: dict[str, Any] = {
    "dt": 0.002,                 # MuJoCo step (s); the Unitree deployment recipe uses the same
    "realtime_factor": 1.0,      # 1 = wall time; >1 = faster than life (streams look sped up)
    "steps_per_tick": 5,         # physics steps between sleeps; 5 × 2 ms = a 10 ms scheduling grain
    "render_width": 640,
    "render_height": 480,
    "stream_fps": 12,            # MJPEG frame rate for /stream and /stream/<camera>
    "jpeg_quality": 70,
    "third_person": True,        # whether /stream (chase camera) is offered
    "lidar_z_offset_m": 0.05,    # rays leave the base origin this much higher (follows the body)
    "chase_back_m": 2.2,         # chase camera fallback offsets; a body may override at spawn
    "chase_up_m": 1.0,
    "chase_clear_m": 0.15,       # when a wall blocks the chase camera, stop this far before it
    "chase_min_m": 0.8,          # …but never closer than this (inside the robot otherwise)
    "render_timeout_s": 10.0,    # a render request that takes longer than this is reported failed
    "sim_dead_wall_s": 5.0,      # /health reports the physics thread dead after this much silence
}

SPAWN_POINT_START = "start"      # layout.START_POS_XY / START_YAW (the arena's documented entry)
SPAWN_POINT_HOME = "home"        # layout.ROBOT_HOME_XY / ROBOT_HOME_YAW

CAMERA_PREFIX = "camera:"


def _yaw_of(quat_wxyz) -> float:
    w, x, y, z = (float(v) for v in quat_wxyz)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _rot_of(quat_wxyz) -> np.ndarray:
    r = np.zeros(9)
    mujoco.mju_quat2Mat(r, np.asarray(quat_wxyz, np.float64))
    return r.reshape(3, 3)


def _load_yaml(path: str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---- the scene library (alice-house) ---------------------------------------------------------------
class SceneLayout:
    """A scene's layout module from the assets library: spawn points, rooms, ceiling group.

    Loaded through the library's own ``scenes/manifest.py`` (it knows how to import a layout with
    the right sys.path); the world never assumes the library's internal structure. Optional: an
    arena given with ``--arena`` has no layout and everything here degrades to "unknown".
    """

    def __init__(self, assets_root: str, scene: str) -> None:
        manifest = os.path.join(assets_root, "scenes", "manifest.py")
        if not os.path.isfile(manifest):
            raise FileNotFoundError(f"no scene manifest at {manifest}; is the worlds/ submodule initialised "
                                    f"(git submodule update --init --recursive)?")
        spec = importlib.util.spec_from_file_location("nerv_assets_scenes_manifest", manifest)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.scene = scene
        self.layout = mod.load_layout(scene)

    def spawn_pose(self, point: str) -> tuple[float, float, float]:
        L = self.layout
        if point == SPAWN_POINT_HOME:
            (x, y), yaw = L.ROBOT_HOME_XY, getattr(L, "ROBOT_HOME_YAW", 0.0)
        elif point == SPAWN_POINT_START:
            (x, y), yaw = L.START_POS_XY, getattr(L, "START_YAW", 0.0)
        else:
            raise ValueError(f"spawn.point must be {SPAWN_POINT_START!r} or {SPAWN_POINT_HOME!r}, "
                             f"got {point!r}")
        return float(x), float(y), float(yaw)

    def room(self, x: float, y: float, z: float) -> dict:
        L = self.layout
        try:
            key = L.room_at(x, y, z)          # multi-storey layouts take z
        except TypeError:
            key = L.room_at(x, y)
        label = L.room_label(key) if hasattr(L, "room_label") else (key or "")
        return {"room_key": key, "room_label": label}

    @property
    def ceiling_group(self) -> int | None:
        g = getattr(self.layout, "CEILING_GROUP", None)
        return int(g) if g is not None else None


# ---- rendering: one thread owns the GL context --------------------------------------------------------
class RenderService:
    """All offscreen rendering happens on this one thread (EGL contexts are thread-affine).

    Callers submit ``fn(renderer) -> result`` and block for the result. The MuJoCo renderer is
    created lazily on the thread, so a machine without a usable GL still runs physics; sensor
    requests then fail with the real error instead of a fake frame.
    """

    def __init__(self, model: mujoco.MjModel, width: int, height: int, timeout_s: float) -> None:
        self._model = model
        self._w, self._h = int(width), int(height)
        self._timeout = float(timeout_s)
        self._q: queue.Queue = queue.Queue()
        self._renderer: mujoco.Renderer | None = None
        self._init_error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="nerv-world-render", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                fn, done, slot = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._renderer is None and self._init_error is None:
                    try:
                        self._renderer = mujoco.Renderer(self._model, self._h, self._w)
                    except Exception as e:
                        self._init_error = e
                if self._renderer is None:
                    raise RuntimeError(f"offscreen rendering unavailable: {self._init_error}")
                slot["result"] = fn(self._renderer)
            except Exception as e:
                slot["error"] = e
            done.set()
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass

    def run(self, fn: Callable[[mujoco.Renderer], Any]) -> Any:
        done, slot = threading.Event(), {}
        self._q.put((fn, done, slot))
        if not done.wait(self._timeout):
            raise TimeoutError(f"render did not finish within {self._timeout:g}s")
        if "error" in slot:
            raise slot["error"]
        return slot["result"]

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def _jpeg(rgb: np.ndarray, quality: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=int(quality))
    return buf.getvalue()


# ---- the arena ----------------------------------------------------------------------------------------------
class WorldSim:
    """Physics + firmware + sensors for one arena. Thread-safe: every touch of MjData holds ``_lock``."""

    def __init__(self, arena_xml: str, *, physics: dict | None = None, world_name: str = "",
                 body_name: str = "", layout: SceneLayout | None = None, spawn: dict | None = None,
                 ambient: list[dict] | None = None, body_defaults: dict | None = None) -> None:
        self.phys = dict(DEFAULT_PHYSICS)
        self.phys.update(physics or {})
        self.world_name = world_name
        self.body_name = body_name
        self.arena_xml = arena_xml
        self.layout = layout
        self.spawn_cfg = dict(spawn or {})
        self.ambient = list(ambient or [])
        self.body_defaults = dict(body_defaults or {})    # start_height / chase_* / fall_* from body.yaml
        self.epoch = uuid.uuid4().hex

        self.model = mujoco.MjModel.from_xml_path(arena_xml)
        self.data = mujoco.MjData(self.model)
        self.model.opt.timestep = float(self.phys["dt"])
        self._lock = threading.RLock()

        # the body's base: the (single) free joint if there is one, else the root of the first tree
        free = [j for j in range(self.model.njnt) if self.model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE]
        if len(free) > 1:
            raise ValueError(f"the arena has {len(free)} free joints; this world hosts exactly one body "
                             f"and expects the scenery to be static")
        self.has_free_base = bool(free)
        self.free_qadr = int(self.model.jnt_qposadr[free[0]]) if free else -1
        self.free_dadr = int(self.model.jnt_dofadr[free[0]]) if free else -1
        self.base_body = int(self.model.jnt_bodyid[free[0]]) if free else -1   # fixed-base: set at spawn
        self.chase_body = self.base_body

        # spawn state (filled by spawn())
        self.spawned = False
        self.joint_names: list[str] = []
        self.qadr = np.zeros(0, int)
        self.dadr = np.zeros(0, int)
        self.act_id = np.zeros(0, int)
        self.pd_mode = ""
        self.kp = np.zeros(0)
        self.kd = np.zeros(0)
        self.tau_max = np.zeros(0)
        self.default_pos = np.zeros(0)
        self.targets = np.zeros(0)
        self.spawn_extra: dict = {}
        self.spawn_pose: tuple[float, float, float, float] | None = None   # x y z yaw
        self.front_extent_m = 0.0
        self._ray_mask: np.ndarray | None = None
        self._ray_gid = np.zeros(1, np.int32)
        self._imu_acc = np.zeros(3)
        self._prev_base_v = np.zeros(3)

        self.render = RenderService(self.model, self.phys["render_width"], self.phys["render_height"],
                                    self.phys["render_timeout_s"])
        self._chase_cam = mujoco.MjvCamera()
        self._chase_cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        self._chase_opt = mujoco.MjvOption()
        if layout is not None and layout.ceiling_group is not None:
            self._chase_opt.geomgroup[layout.ceiling_group] = 0    # else a chase view is all roof

        self.realtime_ratio = 0.0          # measured sim-seconds per wall-second (EMA)
        self._last_advance_wall = time.perf_counter()
        self._running = False
        self._thread: threading.Thread | None = None
        mujoco.mj_forward(self.model, self.data)

    # ---- physics thread --------------------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="nerv-world-physics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.render.stop()

    def _loop(self) -> None:
        dt = float(self.model.opt.timestep)
        n = max(1, int(self.phys["steps_per_tick"]))
        rtf = max(1e-6, float(self.phys["realtime_factor"]))
        tick_sim = n * dt
        t_wall = time.perf_counter()
        prev_tick = t_wall
        ema = 0.0
        while self._running:
            with self._lock:
                for _ in range(n):
                    self._apply_firmware()
                    mujoco.mj_step(self.model, self.data)
                self._update_imu(tick_sim)
            now = time.perf_counter()
            self._last_advance_wall = now
            # measured sim-seconds per wall-second, smoothed over roughly the last hundred ticks
            ema = 0.98 * ema + 0.02 * (tick_sim / max(1e-9, now - prev_tick))
            prev_tick = now
            self.realtime_ratio = ema
            t_wall += tick_sim / rtf
            lag = t_wall - now
            if lag > 0:
                time.sleep(lag)
            else:
                t_wall = time.perf_counter()     # fell behind: re-sync, never try to catch up

    def _apply_firmware(self) -> None:
        if not self.spawned:
            return
        d = self.data
        if self.pd_mode == PD_POSITION_ACTUATOR:
            d.ctrl[self.act_id] = self.targets
            return
        q = d.qpos[self.qadr]
        tau = self.kp * (self.targets - q)
        if self.pd_mode == PD_EXPLICIT:
            tau = tau - self.kd * d.qvel[self.dadr]
        # implicit: kd already lives in dof_damping; adding -kd*qd here would damp twice and ring
        d.qfrc_applied[self.dadr] = np.clip(tau, -self.tau_max, self.tau_max)

    def _update_imu(self, dt_tick: float) -> None:
        """Body-frame specific force, the way an IMU would read it (finite difference + gravity)."""
        if not self.has_free_base:
            return
        v = np.array(self.data.qvel[self.free_dadr:self.free_dadr + 3])
        acc_w = (v - self._prev_base_v) / max(1e-9, dt_tick)
        self._prev_base_v = v
        R = _rot_of(self.data.qpos[self.free_qadr + 3:self.free_qadr + 7])
        self._imu_acc = R.T @ (acc_w - np.asarray(self.model.opt.gravity))

    # ---- spawn / reset -------------------------------------------------------------------------------------
    def _joint_to_actuator(self) -> dict[int, int]:
        """joint id → actuator id by transmission target, never by name (names differ per model;
        a -1 from mj_name2id would silently drive the *last* actuator)."""
        out: dict[int, int] = {}
        for aid in range(self.model.nu):
            if self.model.actuator_trntype[aid] == mujoco.mjtTrn.mjTRN_JOINT:
                out.setdefault(int(self.model.actuator_trnid[aid, 0]), aid)
        return out

    def spawn(self, msg: dict) -> dict:
        names = list(msg.get("joint_names") or [])
        if not names:
            raise ValueError("spawn needs joint_names")
        mode = str(msg.get("pd_mode") or PD_IMPLICIT)
        if mode not in (PD_IMPLICIT, PD_EXPLICIT, PD_POSITION_ACTUATOR):
            raise ValueError(f"pd_mode must be one of {PD_IMPLICIT}/{PD_EXPLICIT}/{PD_POSITION_ACTUATOR}, "
                             f"got {mode!r}")
        j2a = self._joint_to_actuator()
        qadr, dadr, act, missing, no_act, limits = [], [], [], [], [], []
        for n in names:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, n)
            if jid < 0:
                missing.append(n)
                continue
            qadr.append(int(self.model.jnt_qposadr[jid]))
            dadr.append(int(self.model.jnt_dofadr[jid]))
            aid = j2a.get(jid, -1)
            if aid < 0:
                aid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
            act.append(aid)
            if aid < 0:
                no_act.append(n)
            lim = self.model.jnt_range[jid]
            limits.append([float(lim[0]), float(lim[1])] if self.model.jnt_limited[jid]
                          else [-math.inf, math.inf])
        if missing:
            raise ValueError(f"the arena has no joints named {missing}")
        if mode == PD_POSITION_ACTUATOR and no_act:
            raise ValueError(f"{PD_POSITION_ACTUATOR} needs an actuator per joint; none for {no_act}")

        def vec(key: str, fill: float) -> np.ndarray:
            v = msg.get(key) or []
            if len(v) == 0:
                return np.full(len(names), fill, np.float64)
            if len(v) == 1:
                return np.full(len(names), float(v[0]), np.float64)
            if len(v) != len(names):
                raise ValueError(f"{key} has {len(v)} entries for {len(names)} joints")
            return np.asarray(v, np.float64)

        with self._lock:
            self.joint_names = names
            self.qadr, self.dadr, self.act_id = np.asarray(qadr), np.asarray(dadr), np.asarray(act)
            self.pd_mode = mode
            self.kp, self.kd = vec("kp", 0.0), vec("kd", 0.0)
            self.tau_max = vec("torque_limit", math.inf)
            dp = msg.get("default_pos") or []
            self.default_pos = (np.asarray(dp, np.float64) if len(dp) == len(names)
                                else np.array(self.model.qpos0[self.qadr]))
            self.spawn_extra = dict(self.body_defaults)
            self.spawn_extra.update(msg.get("extra") or {})
            if not self.has_free_base:
                self.base_body = int(self.model.body_rootid[self.model.jnt_bodyid[
                    mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, names[0])]])
            self.chase_body = self._resolve_chase_body()
            self.spawn_pose = self._resolve_spawn_pose()
            self._configure_damping()
            self._build_ray_mask()
            self._place()
            self._measure_front_extent()
            self.spawned = True
        return {"joint_names": names, "joint_limits": limits, "epoch": self.epoch,
                "dt": float(self.model.opt.timestep), "has_free_base": self.has_free_base,
                "pd_mode": mode, "front_extent_m": round(self.front_extent_m, 4)}

    def _configure_damping(self) -> None:
        if self.pd_mode == PD_POSITION_ACTUATOR:
            return                      # the MJCF's own actuator/joint parameters are the firmware
        for i, d in enumerate(self.dadr):
            # coulomb friction in the MJCF is a hand-controller default; training had none of it
            self.model.dof_frictionloss[d] = 0.0
            self.model.dof_damping[d] = float(self.kd[i]) if self.pd_mode == PD_IMPLICIT else 0.0

    def _resolve_chase_body(self) -> int:
        name = str(self.spawn_extra.get("chase_body") or "")
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name) if name else -1
        if name and bid < 0:
            print(f"[nerv world] chase_body {name!r} not in the model; chase camera follows the base",
                  file=sys.stderr)
        return bid if bid >= 0 else self.base_body

    def _resolve_spawn_pose(self) -> tuple[float, float, float, float] | None:
        if not self.has_free_base:
            return None
        q0 = self.model.qpos0[self.free_qadr:self.free_qadr + 7]
        x, y, z, yaw = float(q0[0]), float(q0[1]), float(q0[2]), _yaw_of(q0[3:7])
        src = self.spawn_cfg.get("source", "")
        if src == "layout":
            if self.layout is None:
                raise RuntimeError("world.yaml spawn.source is 'layout' but no scene layout was loaded")
            x, y, yaw = self.layout.spawn_pose(str(self.spawn_cfg.get("point") or SPAWN_POINT_START))
        else:
            x = float(self.spawn_cfg.get("x", x))
            y = float(self.spawn_cfg.get("y", y))
            yaw = float(self.spawn_cfg.get("yaw", yaw))
        if "start_height" in self.spawn_extra:
            z = float(self.spawn_extra["start_height"])
        elif "z" in self.spawn_cfg:
            z = float(self.spawn_cfg["z"])
        return x, y, z, yaw

    def _place(self) -> None:
        """Body back at the spawn pose, joints at default, everything at rest."""
        mujoco.mj_resetData(self.model, self.data)
        if self.spawn_pose is not None:
            x, y, z, yaw = self.spawn_pose
            a = self.free_qadr
            self.data.qpos[a:a + 3] = [x, y, z]
            self.data.qpos[a + 3:a + 7] = [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]
        self.data.qpos[self.qadr] = self.default_pos
        self.data.qvel[:] = 0.0
        self.data.qfrc_applied[:] = 0.0
        self.targets = np.array(self.default_pos)
        self._prev_base_v[:] = 0.0
        self._imu_acc[:] = 0.0
        if self.pd_mode == PD_POSITION_ACTUATOR:
            self.data.ctrl[self.act_id] = self.targets
        mujoco.mj_forward(self.model, self.data)

    def reset(self) -> None:
        with self._lock:
            if self.spawned:
                self._place()
            else:
                mujoco.mj_resetData(self.model, self.data)
                mujoco.mj_forward(self.model, self.data)

    def _measure_front_extent(self) -> None:
        """How far the body reaches ahead of its base origin (m), from the geoms' bounding spheres.
        A fact of the model, measured so a brake distance can be built on it instead of guessed."""
        if self.base_body < 0:
            self.front_extent_m = 0.0
            return
        R = _rot_of(self.data.qpos[self.free_qadr + 3:self.free_qadr + 7]) if self.has_free_base \
            else np.eye(3)
        fwd, origin = R[:, 0], self.data.xpos[self.base_body]
        best = 0.0
        for g in range(self.model.ngeom):
            if self.model.body_rootid[self.model.geom_bodyid[g]] != self.base_body:
                continue
            best = max(best, float(np.dot(self.data.geom_xpos[g] - origin, fwd))
                       + float(self.model.geom_rbound[g]))
        self.front_extent_m = best

    def _build_ray_mask(self) -> None:
        """Rays must see the scenery and not the body: mj_ray filters by geom *group* (6 bits) plus
        one excluded body. The body's subtree and the scenery therefore need disjoint groups
        (alice-house convention: robot 2/3, house 0/1/4/5). If they clash we fall back to
        excluding only the base body and say so — a fixed-base arm's rays are not load-bearing."""
        m = self.model
        is_self = [m.body_rootid[m.geom_bodyid[g]] == self.base_body for g in range(m.ngeom)]
        self_groups = {int(m.geom_group[g]) for g in range(m.ngeom) if is_self[g]}
        world_groups = {int(m.geom_group[g]) for g in range(m.ngeom) if not is_self[g]}
        clash = self_groups & world_groups
        bad = sorted(g for g in world_groups if not 0 <= g < mujoco.mjNGROUP)
        if clash or bad:
            print(f"[nerv world] geom groups: body {sorted(self_groups)} scenery {sorted(world_groups)}"
                  f"{' clash ' + str(sorted(clash)) if clash else ''}"
                  f"{' out-of-range ' + str(bad) if bad else ''}; rays exclude only the base body",
                  file=sys.stderr)
            self._ray_mask = None
            return
        mask = np.zeros(mujoco.mjNGROUP, np.uint8)
        for g in world_groups:
            mask[g] = 1
        self._ray_mask = mask

    # ---- bus data plane -----------------------------------------------------------------------------------
    def _base_pose(self) -> tuple[np.ndarray, np.ndarray, float]:
        """(xyz, quat wxyz, yaw) of the base. Fixed-base bodies: from the body frame."""
        if self.has_free_base:
            a = self.free_qadr
            quat = np.array(self.data.qpos[a + 3:a + 7])
            return np.array(self.data.qpos[a:a + 3]), quat, _yaw_of(quat)
        if self.base_body >= 0:
            quat = np.array(self.data.xquat[self.base_body])
            return np.array(self.data.xpos[self.base_body]), quat, _yaw_of(quat)
        return np.zeros(3), np.array([1.0, 0, 0, 0]), 0.0

    def read(self) -> dict:
        with self._lock:
            if not self.spawned:
                raise RuntimeError("nothing spawned yet; spawn first")
            d = self.data
            out: dict[str, Any] = {"t": float(d.time),
                                   "joint_pos": d.qpos[self.qadr].tolist(),
                                   "joint_vel": d.qvel[self.dadr].tolist(), "extra": {}}
            if self.has_free_base:
                xyz, quat, yaw = self._base_pose()
                a = self.free_dadr
                out.update({"imu_quat": quat.tolist(),
                            "imu_gyro": d.qvel[a + 3:a + 6].tolist(),   # already body-frame in MuJoCo
                            "imu_acc": self._imu_acc.tolist(),
                            "odom_xy": xyz[:2].tolist(), "odom_yaw": float(yaw),
                            "base_height": float(xyz[2])})
            else:
                out.update({"imu_quat": [], "imu_gyro": [], "imu_acc": [], "odom_xy": [],
                            "odom_yaw": 0.0, "base_height": 0.0})
            return out

    def write(self, msg: dict) -> None:
        t = msg.get("targets")
        if t is None or len(t) != len(self.joint_names):
            raise ValueError(f"write needs {len(self.joint_names)} targets, got "
                             f"{0 if t is None else len(t)}")
        with self._lock:
            if not self.spawned:
                raise RuntimeError("nothing spawned yet; spawn first")
            self.targets = np.asarray(t, np.float64)
            if msg.get("kp") is not None and len(msg["kp"]) == len(self.joint_names):
                self.kp = np.asarray(msg["kp"], np.float64)
            if msg.get("kd") is not None and len(msg["kd"]) == len(self.joint_names):
                self.kd = np.asarray(msg["kd"], np.float64)
                self._configure_damping()

    def rays(self, angles_deg: list[float], max_range_m: float) -> list[float]:
        max_range_m = float(max_range_m)
        with self._lock:
            xyz, _quat, yaw = self._base_pose()
            pnt = np.array([xyz[0], xyz[1], xyz[2] + float(self.phys["lidar_z_offset_m"])])
            out = []
            for a in angles_deg:
                ang = yaw + math.radians(float(a))
                vec = np.array([math.cos(ang), math.sin(ang), 0.0])
                d = self._ray(pnt, vec)
                out.append(max_range_m if d < 0 else min(d, max_range_m))
        return out

    def _ray(self, pnt: np.ndarray, vec: np.ndarray) -> float:
        """Distance to the first scenery hit along vec, or -1. flg_static=1: walls are static geoms."""
        return float(mujoco.mj_ray(self.model, self.data, pnt, vec, self._ray_mask, 1,
                                   self.base_body if self.base_body >= 0 else -1, self._ray_gid))

    # ---- sensors ----------------------------------------------------------------------------------------------
    def model_cameras(self) -> list[str]:
        return [mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_CAMERA, i) or f"camera{i}"
                for i in range(self.model.ncam)]

    def sensor_names(self) -> list[str]:
        names = [CAMERA_PREFIX + c for c in self.model_cameras()]
        for a in self.ambient:
            if a.get("name") and a["name"] not in names:
                names.append(a["name"])
        return names

    def _camera_for(self, name: str) -> str:
        for a in self.ambient:
            if name in (a.get("name"), a.get("camera")) and a.get("camera"):
                return a["camera"]
        bare = name[len(CAMERA_PREFIX):] if name.startswith(CAMERA_PREFIX) else name
        if mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, bare) < 0:
            raise KeyError(f"no sensor named {name!r}; have {self.sensor_names()}")
        return bare

    def render_camera(self, name: str) -> tuple[np.ndarray, float]:
        cam = self._camera_for(name)

        def job(r: mujoco.Renderer):
            with self._lock:
                r.update_scene(self.data, camera=cam)
                t = float(self.data.time)
            return r.render().copy(), t
        return self.render.run(job)

    def render_chase(self) -> tuple[np.ndarray, float]:
        """Third-person view from behind and above the chase body. Humans only, never the brain."""
        cam, opt = self._chase_cam, self._chase_opt
        back_m = float(self.spawn_extra.get("chase_back_m") or self.phys["chase_back_m"])
        up_m = float(self.spawn_extra.get("chase_up_m") or self.phys["chase_up_m"])

        def job(r: mujoco.Renderer):
            with self._lock:
                body = self.chase_body if self.chase_body >= 0 else 0
                pos = np.array(self.data.xpos[body])
                _xyz, _q, yaw = self._base_pose()
                cam.lookat[:] = pos
                # azimuth is "where the camera looks", so it equals the heading: camera stays behind
                cam.azimuth = math.degrees(yaw)
                cam.elevation = -math.degrees(math.atan2(up_m, back_m))
                want = math.hypot(back_m, up_m)
                el, az = math.radians(cam.elevation), math.radians(cam.azimuth)
                back = np.array([-math.cos(el) * math.cos(az), -math.cos(el) * math.sin(az), math.sin(-el)])
                hit = self._ray(pos, back) if self.spawned else -1.0
                cam.distance = (max(float(self.phys["chase_min_m"]), min(want, hit - float(self.phys["chase_clear_m"])))
                                if hit > 0 else want)
                r.update_scene(self.data, camera=cam, scene_option=opt)
                t = float(self.data.time)
            return r.render().copy(), t
        return self.render.run(job)

    # ---- god's eye ----------------------------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            xyz, quat, yaw = self._base_pose()
            R = _rot_of(quat)
            tilt = math.acos(max(-1.0, min(1.0, float(R[2, 2]))))
            t = float(self.data.time)
        fall_tilt = self.spawn_extra.get("fall_tilt_rad")
        fall_h = self.spawn_extra.get("fall_height_m")
        fallen = None
        if self.has_free_base and fall_tilt is not None and fall_h is not None:
            fallen = bool(tilt > float(fall_tilt) or xyz[2] < float(fall_h))
        out = {"sim_time": round(t, 3), "spawned": self.spawned, "pd_mode": self.pd_mode,
               "base": {"x": round(float(xyz[0]), 3), "y": round(float(xyz[1]), 3), "z": round(float(xyz[2]), 3),
                        "yaw_deg": round(math.degrees(yaw) % 360.0, 1)},
               "tilt_deg": round(math.degrees(tilt), 1), "fallen": fallen,
               "realtime_ratio": round(self.realtime_ratio, 2), "epoch": self.epoch}
        if self.layout is not None:
            try:
                out.update(self.layout.room(float(xyz[0]), float(xyz[1]), float(xyz[2])))
            except Exception as e:
                out["room_error"] = f"{type(e).__name__}: {e}"
        return out

    def physics_alive(self) -> bool:
        return self._running and (time.perf_counter() - self._last_advance_wall) < float(self.phys["sim_dead_wall_s"])


# ---- bus handler ----------------------------------------------------------------------------------------------
def make_bus_handler(sim: WorldSim) -> Callable[[dict], dict]:
    from ..nerve import wire

    def handle(msg: dict) -> dict:
        op = msg.get("op")
        if op == OP_SPAWN:
            return sim.spawn(msg)
        if op == OP_READ:
            return sim.read()
        if op == OP_WRITE:
            sim.write(msg)
            return {}
        if op == OP_RESET:
            sim.reset()
            return {}
        if op == OP_SENSORS:
            return {"sensors": sim.sensor_names()}
        if op == OP_SENSOR:
            rgb, t = sim.render_camera(str(msg.get("name", "")))
            return {"mime": "image/jpeg", "data": wire.b64e(_jpeg(rgb, sim.phys["jpeg_quality"])), "t": t}
        if op == OP_RAYS:
            return {"ranges": sim.rays(list(msg.get("angles_deg") or []),
                                       float(msg.get("max_range_m", 0.0)))}
        if op == OP_EPOCH:
            return {"epoch": sim.epoch}
        if op == OP_CLOSE:
            return {}
        raise ValueError(f"unknown bus op {op!r}")
    return handle


# ---- HTTP control plane -----------------------------------------------------------------------------------
def build_app(sim: WorldSim, cors_origins: list[str]):
    import asyncio

    import anyio.to_thread
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

    app = FastAPI(title=f"NERV world · {sim.world_name or os.path.basename(sim.arena_xml)}")
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])
    fps = max(1, int(sim.phys["stream_fps"]))
    quality = int(sim.phys["jpeg_quality"])

    @app.get("/health")
    def health() -> dict:
        return {"ok": sim.physics_alive(), "node": "world", "world": sim.world_name, "body": sim.body_name,
                "epoch": sim.epoch, "sim_time": round(float(sim.data.time), 3),
                "realtime_factor": float(sim.phys["realtime_factor"]),
                "realtime_ratio": round(sim.realtime_ratio, 2), "spawned": sim.spawned}

    @app.get("/status")
    def status() -> dict:
        return sim.status()

    @app.get("/sensors")
    def sensors() -> dict:
        return {"sensors": sim.sensor_names()}

    @app.get("/sensors/{name}")
    async def sensor(name: str):
        try:
            rgb, t = await anyio.to_thread.run_sync(sim.render_camera, name)
        except KeyError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=503)
        return Response(content=_jpeg(rgb, quality), media_type="image/jpeg",
                        headers={"X-Sim-Time": f"{t:.3f}"})

    @app.post("/reset")
    def reset() -> dict:
        sim.reset()
        return {"ok": True, "message": "body back at the spawn pose"}

    def _mjpeg(render: Callable[[], tuple[np.ndarray, float]]):
        async def gen():
            while True:
                try:
                    rgb, _t = await anyio.to_thread.run_sync(render)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + _jpeg(rgb, quality) + b"\r\n"
                except Exception:
                    pass          # a failed frame is skipped, never replaced by a stale/fake one
                await asyncio.sleep(1.0 / fps)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/stream")
    async def stream():
        if not sim.phys["third_person"]:
            return JSONResponse({"ok": False, "error": "third_person is off in world.yaml"}, status_code=404)
        return _mjpeg(sim.render_chase)

    @app.get("/stream/{camera}")
    async def stream_camera(camera: str):
        try:
            sim._camera_for(camera)
        except KeyError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        return _mjpeg(lambda: sim.render_camera(camera))

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        cams = "".join(f"<figure><figcaption>{CAMERA_PREFIX}{c}</figcaption>"
                       f"<img src='/stream/{c}' style='max-width:480px;border:1px solid #ccc'></figure>"
                       for c in sim.model_cameras())
        chase = ("<figure><figcaption>chase camera (humans only)</figcaption>"
                 "<img src='/stream' style='max-width:640px;border:1px solid #ccc'></figure>"
                 if sim.phys["third_person"] else "")
        return (f"<html><body style='font-family:system-ui;margin:2rem'>"
                f"<h2>NERV world · {sim.world_name or os.path.basename(sim.arena_xml)}</h2>"
                f"<p>body {sim.body_name or '-'} · epoch {sim.epoch[:8]} · "
                f"<a href='/health'>/health</a> · <a href='/status'>/status</a> · "
                f"<a href='/sensors'>/sensors</a> · "
                f"<button onclick=\"fetch('/reset',{{method:'POST'}})\">Reset</button></p>"
                f"<div style='display:flex;flex-wrap:wrap;gap:1rem'>{chase}{cams}</div></body></html>")

    return app


# ---- entry point ---------------------------------------------------------------------------------------------
def _body_defaults_from_registry(body) -> dict:
    """What the world needs to know about the body it hosts, from body.yaml: spawn height, chase
    camera offsets, fall thresholds. A spawn message's `extra` overrides any of these."""
    out: dict = {}
    act = dict(body.actuators or {})
    for k in ("start_height", "chase_body", "chase_back_m", "chase_up_m"):
        if k in act:
            out[k] = act[k]
    # `locomotion` is not a BodySpec field (pydantic drops it): read the yaml for the fall thresholds
    try:
        raw = _load_yaml(os.path.join(body.dir, "body.yaml"))
        loco = raw.get("locomotion") or {}
        for k in ("fall_tilt_rad", "fall_height_m"):
            if k in loco:
                out[k] = float(loco[k])
    except Exception:
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nerv world node",
                                 description="python -m nerv.world --world W --body B --http-port P --bus-port Q "
                                             "| --arena scene.xml --http-port P --bus-port Q")
    ap.add_argument("--world", default="", help="world name from the registry (worlds/<name>/world.yaml)")
    ap.add_argument("--body", default="", help="body name from the registry; picks the arena via supports")
    ap.add_argument("--arena", default="", help="registry-free: path to an MJCF scene")
    ap.add_argument("--http-port", type=int, required=True)
    ap.add_argument("--bus-port", type=int, required=True)
    ap.add_argument("--host", default=os.environ.get("NERV_NODE_BIND_HOST", "127.0.0.1"))
    ap.add_argument("--cors", default=os.environ.get("NERV_CORS_ORIGINS", "http://localhost:8100"))
    a = ap.parse_args(argv)

    physics: dict = {}
    layout: SceneLayout | None = None
    spawn: dict = {}
    ambient: list[dict] = []
    body_defaults: dict = {}
    world_name, body_name = "", a.body
    if a.arena:
        arena = os.path.abspath(a.arena)
        world_name = os.path.splitext(os.path.basename(arena))[0]
    else:
        if not (a.world and a.body):
            print("give --world and --body (registry) or --arena (a scene xml)", file=sys.stderr)
            return 2
        from ..platform.registry import Registry
        reg = Registry()
        w = reg.world(a.world)
        if w.kind != "sim" or w.engine != "mujoco":
            print(f"world {w.name} is {w.kind}/{w.engine or '-'}; this node only runs mujoco sims", file=sys.stderr)
            return 2
        if a.body not in w.supports:
            print(f"world {w.name} has no arena for body {a.body!r}; supports: {list(w.supports)}", file=sys.stderr)
            return 2
        if not w.assets_root:
            print(f"world {w.name}: assets_root is empty (is its environment variable set?)", file=sys.stderr)
            return 2
        arena = os.path.join(w.assets_root, w.supports[a.body])
        world_name = w.name
        physics = dict(w.physics or {})
        spawn = dict(w.spawn or {})
        ambient = [s.model_dump() for s in w.ambient]
        if spawn.get("source") == "layout":
            layout = SceneLayout(w.assets_root, str(spawn.get("scene") or w.name))
        try:
            body_defaults = _body_defaults_from_registry(reg.body(a.body))
        except KeyError as e:
            print(f"[nerv world] {e}; spawning with world defaults", file=sys.stderr)
    if not os.path.isfile(arena):
        print(f"arena not found: {arena}", file=sys.stderr)
        return 2

    sim = WorldSim(arena, physics=physics, world_name=world_name, body_name=body_name, layout=layout,
                   spawn=spawn, ambient=ambient, body_defaults=body_defaults)
    sim.start()
    bus = BusServer(f"tcp://{a.host}:{a.bus_port}", make_bus_handler(sim))
    bus.start()
    app = build_app(sim, [o.strip() for o in a.cors.split(",") if o.strip()])
    print(f"[nerv world] {world_name} body={body_name or '-'} arena={os.path.basename(arena)} "
          f"cameras={sim.model_cameras()} http://{a.host}:{a.http_port} bus=tcp://{a.host}:{a.bus_port}",
          flush=True)
    import uvicorn
    try:
        uvicorn.run(app, host=a.host, port=a.http_port, log_level="warning")
    finally:
        bus.stop()
        sim.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
