"""Generate a frozen multi-robot model from source arenas using MuJoCo's compiler.

The input files and third-party robot trees are never rewritten. Policy joint names
remain local; compiled names carry a robot instance prefix. One model, data object
and clock own all contacts, including contacts between robots.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import re
import threading
import time
import uuid

import mujoco
import numpy as np

from .mujoco_node import WorldSim, robot_root, CAMERA_PREFIX
from .clearance import RAY_MASK


def compose(arena: str, roster: list[dict]):
    if not roster or len({r['robot_instance_id'] for r in roster}) != len(roster):
        raise ValueError('A frozen roster needs distinct robot instance IDs')
    for robot in roster:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', robot['robot_instance_id']):
            raise ValueError('Invalid robot instance ID')
        if set(robot.get('spawn', {})) - {'x', 'y', 'z', 'yaw', 'initial_joints'}:
            raise ValueError('Shared spawn must explicitly describe a frozen pose')
    source = mujoco.MjModel.from_xml_path(arena)
    root, _ = robot_root(source)
    root_name = mujoco.mj_id2name(source, mujoco.mjtObj.mjOBJ_BODY, root)
    scene = mujoco.MjSpec.from_file(arena)
    # Single-robot keyframes cannot describe this topology. Inputs remain intact.
    for key in list(scene.keys):
        scene.delete(key)
    scene.delete(scene.body(root_name))
    roots = {}
    for robot in roster:
        child = mujoco.MjSpec.from_file(arena)
        for key in list(child.keys):
            child.delete(key)
        prefix = robot['robot_instance_id'] + '/'
        scene.worldbody.add_frame().attach_body(child.body(root_name), prefix=prefix)
        roots[robot['robot_instance_id']] = prefix + root_name
    model = scene.compile()
    receipt = {'generator': 'nerv.world.composition:1',
               'source_sha256': hashlib.sha256(Path(arena).read_bytes()).hexdigest(),
               'roster': copy.deepcopy(roster), 'actuators': model.nu,
               'roots': roots}
    return model, receipt


class RobotView(WorldSim):
    """An explicitly bound endpoint; it cannot reset or advance the shared world."""
    def __init__(self, owner, record, root_name):
        self.owner = owner
        self.robot_instance_id = record['robot_instance_id']
        self.prefix = self.robot_instance_id + '/'
        spawn = dict(record['spawn'])
        spawn['initial_joints'] = {self.prefix + k: v for k, v in spawn.get('initial_joints', {}).items()}
        super().__init__(owner.arena, physics=owner.physics, world_name=owner.world_instance_id,
                         body_name=self.robot_instance_id, spawn=spawn,
                         body_defaults=record.get('body_defaults'), shared_model=owner.model,
                         shared_data=owner.data, shared_lock=owner.lock, root_name=root_name)
        self.epoch = owner.epoch
        self.instance_receipt = owner.receipt

    def start(self):
        raise ValueError('Only the shared World advances physics')

    def stop(self):
        self.render.stop()

    def reset(self):
        raise ValueError('Shared World reset requires the operator')

    def _place(self):
        # Initial attachment only. Never reset another robot or a scene prop.
        if self.spawn_pose is not None:
            x, y, z, yaw = self.spawn_pose
            a = self.free_qadr
            self.data.qpos[a:a+7] = [x, y, z, math.cos(yaw/2), 0, 0, math.sin(yaw/2)]
            self.data.qvel[self.free_dadr:self.free_dadr+6] = 0
        self.data.qpos[self.qadr] = self.default_pos
        self.data.qvel[self.dadr] = 0
        self.data.qfrc_applied[self.dadr] = 0
        self.targets = np.array(self.default_pos)
        self._prev_base_v[:] = 0
        self._imu_acc[:] = 0
        mujoco.mj_forward(self.model, self.data)

    def spawn(self, msg):
        with self._lock:
            if self.spawned:
                raise ValueError('Robot already attached; topology requires a new World')
            names = list(msg.get('joint_names') or [])
            extra = dict(msg.get('extra') or {})
            if extra.get('chase_body'):
                extra['chase_body'] = self.prefix + extra['chase_body']
            result = super().spawn({**msg, 'joint_names': [self.prefix+n for n in names], 'extra': extra})
            self.joint_names = names
            return {**result, 'joint_names': names, 'robot_instance_id': self.robot_instance_id,
                    'world_instance_id': self.owner.world_instance_id}

    def model_cameras(self):
        return [name.removeprefix(self.prefix) for name in super().model_cameras() if name.startswith(self.prefix)]

    def _camera_for(self, name):
        bare = name.removeprefix(CAMERA_PREFIX)
        if bare not in self.model_cameras():
            raise KeyError('Sensor does not belong to this robot')
        return self.prefix + bare

    def _build_ray_mask(self):
        # ClearanceScanner constructs a physical-only model view, excluding exactly
        # our entire tree and retaining the other robot even with equal geom groups.
        self._ray_mask = None

    def _ray(self, pnt, vec):
        scanner = getattr(self, '_clearance_scanner', None)
        if scanner is None:
            return -1.0
        scanner._physical_view()
        return float(mujoco.mj_ray(scanner._view, self.data, pnt, vec, RAY_MASK, 1, -1, self._ray_gid))

    def clearance(self, query):
        return super().clearance({**query, 'support_body_names':
                                  [self.prefix+n for n in query.get('support_body_names', [])]})


class SharedWorld:
    """Owns one physics loop. Physics starts only after every roster slot attaches."""
    def __init__(self, arena, roster, *, world_instance_id=None, physics=None):
        self.arena = arena
        self.world_instance_id = world_instance_id or 'world_' + uuid.uuid4().hex
        self.epoch = uuid.uuid4().hex
        self.model, self.receipt = compose(arena, roster)
        self.receipt.update(world_instance_id=self.world_instance_id, epoch=self.epoch)
        self.data = mujoco.MjData(self.model)
        self.lock = threading.RLock()
        self.physics = dict(physics or {})
        self.views = {r['robot_instance_id']: RobotView(self, r, self.receipt['roots'][r['robot_instance_id']]) for r in roster}
        self.running = False
        self.error = None
        self.thread = None

    def view(self, robot_instance_id):
        if robot_instance_id not in self.views:
            raise ValueError('An explicit robot instance is required')
        return self.views[robot_instance_id]

    def step(self, count=1):
        with self.lock:
            if not all(v.spawned for v in self.views.values()):
                return False
            for _ in range(count):
                for view in self.views.values():
                    view._apply_firmware()
                mujoco.mj_step(self.model, self.data)
            now = time.perf_counter()
            for view in self.views.values():
                view._update_imu(count * self.model.opt.timestep)
                view._last_advance_wall = now
            return True

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name='nerv-shared-world', daemon=True)
        for view in self.views.values():
            view._running, view._thread = True, self.thread
        self.thread.start()

    def _loop(self):
        sample = next(iter(self.views.values()))
        n = max(1, int(sample.phys['steps_per_tick']))
        interval = n * self.model.opt.timestep / float(sample.phys['realtime_factor'])
        try:
            while self.running:
                start = time.perf_counter()
                advanced = self.step(n)
                elapsed = time.perf_counter() - start
                for view in self.views.values():
                    view.realtime_ratio = n*self.model.opt.timestep/max(interval, elapsed) if advanced else 0
                time.sleep(max(float(sample.phys['scheduler_yield_s']), interval-elapsed))
        except Exception as error:
            self.error = str(error)
        finally:
            self.running = False
            for view in self.views.values():
                view._running = False

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(5)
        for view in self.views.values():
            view.stop()
