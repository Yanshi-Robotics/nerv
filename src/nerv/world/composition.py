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

from .mujoco_node import WorldSim, robot_root, CAMERA_PREFIX, DEFAULT_PHYSICS
from .clearance import RAY_MASK


def compose(arena: str, roster: list[dict]):
    if len({r['robot_instance_id'] for r in roster}) != len(roster):
        raise ValueError('A frozen roster needs distinct robot instance IDs')
    for robot in roster:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', robot['robot_instance_id']):
            raise ValueError('Invalid robot instance ID')
        if not {'x', 'y', 'z', 'yaw'} <= set(robot.get('spawn', {})) or set(robot['spawn']) - {'x', 'y', 'z', 'yaw', 'initial_joints'}:
            raise ValueError('Shared spawn must explicitly describe a frozen pose')
        if any(type(robot['spawn'][k]) not in (int, float) or not math.isfinite(robot['spawn'][k]) for k in ('x', 'y', 'z', 'yaw')):
            raise ValueError('Spawn coordinates must be finite')
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
        self.spawn_pose = tuple(float(record['spawn'][key]) for key in ('x', 'y', 'z', 'yaw'))
        self._place()

    def read(self):
        value = super().read()
        value['extra'].update(world_instance_id=self.owner.world_instance_id,
                              robot_instance_id=self.robot_instance_id,
                              topology_revision=self.owner.topology_revision)
        return value

    def write(self, msg):
        with self._lock:
            revision = msg.get("topology_revision")
            if revision != self.owner.topology_revision and (revision is not None or self.owner.topology_revision > 1):
                raise ValueError("Actuator command belongs to an obsolete topology")
            return super().write(msg)

    def operator_geometry(self):
        """Ground truth for independent scoring; never part of a sensor response."""
        with self._lock:
            selected = []
            for gid in range(self.model.ngeom):
                body = int(self.model.geom_bodyid[gid])
                while body and body != self.base_body:
                    body = int(self.model.body_parentid[body])
                if body != self.base_body or not (self.model.geom_contype[gid] or self.model.geom_conaffinity[gid]):
                    continue
                selected.append(gid)
            boxes = self.owner.geometry_boxes(selected)
            bounds = np.array([box['bounds'] for box in boxes])
            return {'robot_instance_id':self.robot_instance_id, 'world_instance_id':self.owner.world_instance_id,
                    'epoch':self.epoch, 'sim_time':float(self.data.time),
                    'bounds':[bounds[:,0,:].min(axis=0).tolist(), bounds[:,1,:].max(axis=0).tolist()],
                    'joint_velocity':self.data.qvel[self.dadr].tolist(),
                    'base_velocity':self.data.qvel[self.free_dadr:self.free_dadr+6].tolist(),
                    'status':self.status()}

    def start(self):
        raise ValueError('Only the shared World advances physics')

    def stop(self):
        self.render.stop()

    def reset(self):
        raise ValueError('Shared World reset requires the operator')

    def _resolve_spawn_pose(self):
        # Frozen operator geometry wins over the single-robot policy default.
        pose = super()._resolve_spawn_pose()
        if pose is not None:
            return pose[0], pose[1], float(self.spawn_cfg['z']), pose[3]
        return None

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
        self._imu_sample_time = None
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
            self._spawn_request = copy.deepcopy(msg)
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
        self.physics = dict(DEFAULT_PHYSICS, **(physics or {}))
        self.model.opt.timestep = float(self.physics['dt'])
        self.topology_revision = 1
        self._topology_lock = threading.Lock()
        self._access = threading.Condition()
        self._readers = 0
        self._changing = False
        self.roster = copy.deepcopy(roster)
        self.views = {}
        try:
            for record in roster:
                rid = record['robot_instance_id']
                self.views[rid] = RobotView(self, record, self.receipt['roots'][rid])
        except Exception:
            for view in self.views.values():
                view.stop()
            raise
        self.running = False
        self.error = None
        self.evidence = None
        self.thread = None

    def endpoint(self, robot_instance_id):
        return RobotEndpoint(self, robot_instance_id)

    def transact(self, roster, *, expected_revision):
        """Called only after Host-authorized quiescence. No in-place compilation.

        All public robot IO enters through RobotEndpoint. During migration the
        old model/data remain untouched. Unsupported persistent state is refused.
        """
        with self._topology_lock:
            if self.evidence is not None and not self.evidence.finished:
                raise ValueError('End the evidence recording before changing topology')
            if expected_revision != self.topology_revision:
                raise ValueError('World topology changed; refresh before retrying')
            with self._access:
                self._changing = True
                self._access.wait_for(lambda: self._readers == 0)
            candidate = None
            retired = []
            try:
                with self.lock:
                    candidate = SharedWorld(self.arena, roster, world_instance_id=self.world_instance_id,
                                            physics=self.physics)
                    for rid, view in candidate.views.items():
                        old = self.views.get(rid)
                        if old is not None and old.spawned:
                            view.spawn(old._spawn_request)
                    migrate_state(self.model, self.data, candidate.model, candidate.data)
                    for rid, view in candidate.views.items():
                        if view.spawned:
                            # Retain a supervised posture, never replay old movement targets.
                            view.targets = candidate.data.qpos[view.qadr].copy()
                            view._prev_base_v[:] = candidate.data.qvel[view.free_dadr:view.free_dadr+3] if view.has_free_base else 0
                    mujoco.mj_forward(candidate.model, candidate.data)
                    if not all(np.isfinite(v).all() for v in (candidate.data.qpos, candidate.data.qvel)):
                        raise ValueError('Non-finite migrated world state')
                    survivors = set(self.views) & set(candidate.views)
                    for contact in candidate.data.contact:
                        if contact.dist >= 0:
                            continue
                        roots = {int(candidate.model.body_rootid[candidate.model.geom_bodyid[g]]) for g in contact.geom}
                        new_roots = {v.base_body for rid, v in candidate.views.items() if rid not in survivors}
                        if roots & new_roots:
                            raise ValueError('New robot intersects existing collision geometry')
                    retired = list(self.views.values())
                    self.model, self.data, self.views = candidate.model, candidate.data, candidate.views
                    self.topology_revision += 1
                    self.roster = copy.deepcopy(roster)
                    self.receipt = dict(candidate.receipt, epoch=self.epoch, topology_revision=self.topology_revision)
                    for view in self.views.values():
                        view.owner, view._lock = self, self.lock
                        view.epoch = self.epoch
                        view.instance_receipt = self.receipt
                        view._running, view._thread = self.running, self.thread
                    candidate = None  # Ownership transferred; do not stop new renderers.
                    return self.receipt
            finally:
                if candidate is not None:
                    candidate.stop()
                for view in retired:
                    view.stop()
                with self._access:
                    self._changing = False
                    self._access.notify_all()

    def geometry_boxes(self, indices=None):
        """Conservative world-space collision AABBs, operator diagnostics only."""
        boxes=[]
        with self.lock:
            for gid in range(self.model.ngeom) if indices is None else indices:
                if not (self.model.geom_contype[gid] or self.model.geom_conaffinity[gid]):
                    continue
                rotation=self.data.geom_xmat[gid].reshape(3,3)
                center=self.data.geom_xpos[gid]+rotation @ self.model.geom_aabb[gid,:3]
                extent=np.abs(rotation) @ self.model.geom_aabb[gid,3:]
                boxes.append({'name':mujoco.mj_id2name(self.model,mujoco.mjtObj.mjOBJ_GEOM,gid),
                              'body_id':int(self.model.geom_bodyid[gid]),
                              'bounds':[(center-extent).tolist(),(center+extent).tolist()]})
        return boxes

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
                if self.evidence is not None:
                    try:
                        self.evidence.step(self)
                    except Exception as error:
                        self.evidence.error = str(error)
                        self.evidence.finished = True
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
        n = max(1, int(self.physics['steps_per_tick']))
        interval = n * self.model.opt.timestep / float(self.physics['realtime_factor'])
        try:
            while self.running:
                start = time.perf_counter()
                advanced = self.step(n)
                elapsed = time.perf_counter() - start
                for view in self.views.values():
                    view.realtime_ratio = n*self.model.opt.timestep/max(interval, elapsed) if advanced else 0
                time.sleep(max(float(self.physics['scheduler_yield_s']), interval-elapsed))
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


class RobotEndpoint:
    """Resolve every request against the current model, including existing HTTP mounts."""
    def __init__(self, owner, robot_id):
        self.owner, self.robot_id = owner, robot_id

    def __getattr__(self, name):
        owner = self.owner
        with owner._access:
            if owner._changing:
                raise RuntimeError('World topology is changing; retry after a fresh observation')
            value = getattr(owner.view(self.robot_id), name)
        if not callable(value):
            return value
        def call(*args, **kwargs):
            with owner._access:
                if owner._changing:
                    raise RuntimeError('World topology is changing; retry after a fresh observation')
                owner._readers += 1
            try:
                return getattr(owner.view(self.robot_id), name)(*args, **kwargs)
            finally:
                with owner._access:
                    owner._readers -= 1
                    owner._access.notify_all()
        return call


def _joint_keys(model):
    result = {}
    for jid in range(model.njnt):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid)
        bid = int(model.jnt_bodyid[jid])
        body = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
        key = name or f'{body}/__joint_{jid-int(model.body_jntadr[bid])}'
        if key in result or not body:
            raise ValueError('State migration requires unique named joint/body identities')
        result[key] = jid
    return result


def migrate_state(old_model, old, new_model, new):
    """Explicit state migration by stable joint identity, never by integer index.

    Current qualified topology is stateless motor actuation without equality,
    mocap or plugins. Refuse extensions until their state mapping is implemented.
    """
    if any(getattr(m, field) for m in (old_model, new_model)
           for field in ('na', 'neq', 'nmocap', 'nplugin')):
        raise ValueError('Topology migration does not support activation, equality, mocap or plugin state')
    old_joints, new_joints = _joint_keys(old_model), _joint_keys(new_model)
    sizes = {int(mujoco.mjtJoint.mjJNT_FREE): (7, 6), int(mujoco.mjtJoint.mjJNT_BALL): (4, 3),
             int(mujoco.mjtJoint.mjJNT_SLIDE): (1, 1), int(mujoco.mjtJoint.mjJNT_HINGE): (1, 1)}
    for key in old_joints.keys() & new_joints.keys():
        a, b = old_joints[key], new_joints[key]
        if old_model.jnt_type[a] != new_model.jnt_type[b]:
            raise ValueError('Surviving joint type changed')
        nq, nv = sizes[int(old_model.jnt_type[a])]
        qa, qb = int(old_model.jnt_qposadr[a]), int(new_model.jnt_qposadr[b])
        va, vb = int(old_model.jnt_dofadr[a]), int(new_model.jnt_dofadr[b])
        new.qpos[qb:qb+nq] = old.qpos[qa:qa+nq]
        for field in ('qvel', 'qacc_warmstart', 'qfrc_applied'):
            getattr(new, field)[vb:vb+nv] = getattr(old, field)[va:va+nv]
    old_bodies = {mujoco.mj_id2name(old_model, mujoco.mjtObj.mjOBJ_BODY, i): i for i in range(old_model.nbody)}
    for i in range(new_model.nbody):
        name = mujoco.mj_id2name(new_model, mujoco.mjtObj.mjOBJ_BODY, i)
        if name in old_bodies and name is not None:
            new.xfrc_applied[i] = old.xfrc_applied[old_bodies[name]]
    if old.userdata.shape != new.userdata.shape:
        raise ValueError('World userdata layout changed')
    new.userdata[:] = old.userdata
    new.time = old.time
