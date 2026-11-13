"""Robot-local standing feedback for the MuJoCo simulation runtime.

This is a motor controller, not an Agent or a navigation policy. Its model contains
one robot and a locally estimated flat support plane. No World, map, target region,
or other robot state is available to it. The released gait remains responsible for
locomotion. Stop confirmation continues to come from measured sensor evidence.

The inverse-dynamics and COM-relative-to-feet cost construction follows the official
MuJoCo LQR tutorial (Apache-2.0):
https://github.com/google-deepmind/mujoco/blob/main/python/LQR.ipynb
We solve a declared finite horizon; neutral global pose modes do not establish an
infinite-horizon stability certificate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np


class LocalStandingController:
    def __init__(self, model_path, policy, state, settings, physics_dt=None):
        import mujoco

        started = time.monotonic()
        self.policy = policy
        self.settings = settings
        for key in ('horizon_s', 'design_timeout_s', 'derivative_epsilon', 'balance_cost'):
            value = settings.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid standing feedback setting: ' + key)
        weights = settings.get('posture_cost', {})
        if ('default' not in weights or not settings.get('foot_bodies')
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                       for v in weights.values())):
            raise ValueError('Standing feedback requires positive posture costs and support bodies')
        path = Path(model_path)
        expected = settings.get('model_sha256')
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Standing model changed after qualification')
        root = ET.parse(path).getroot()
        root.find('compiler').set('meshdir', str(path.resolve().parent / 'meshes'))
        contract = json.loads(Path(policy.dir, 'contract.json').read_text())
        release_dt = float(contract['timing']['physics_dt_s'])
        dt = release_dt if physics_dt is None else float(physics_dt)
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError('World must report a finite positive physics step')
        decimation = round(policy.policy_dt / dt)
        if (policy.pd_mode != 'implicit' or decimation < 1
                or not math.isclose(dt * decimation, policy.policy_dt)):
            raise ValueError('World physics step must divide the implicit-PD command period')
        root.find('option').set('timestep', str(dt))
        ET.SubElement(root.find('worldbody'), 'geom', name='balance_support_plane',
                      type='plane', size='0 0 .1')
        self.model = m = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding='unicode'))
        self.data = d = mujoco.MjData(m)
        self.qadr = np.array([m.joint(n).qposadr[0] for n in policy.joint_names])
        self.dadr = np.array([m.joint(n).dofadr[0] for n in policy.joint_names])
        self.act = np.array([m.actuator(n).id for n in policy.joint_names])
        # The local model must have exactly one free root and the contracted joints.
        free = np.flatnonzero(m.jnt_type == mujoco.mjtJoint.mjJNT_FREE)
        if (len(free) != 1 or m.jnt_qposadr[free[0]] != 0 or m.jnt_dofadr[free[0]] != 0
                or m.nv != len(policy.joint_names) + 6 or m.nu != len(policy.joint_names)):
            raise ValueError('Standing model does not match the isolated robot contract')
        m.dof_frictionloss[self.dadr] = 0
        m.dof_damping[self.dadr] = policy.kd
        self.origin = np.asarray(state.odom_xy, dtype=float)
        self.height_offset = 0.0
        self.previous = None
        self._read(state)
        d.qvel[:] = 0
        mujoco.mj_forward(m, d)
        feet = [m.body(name).id for name in settings['foot_bodies']]
        contact = [g for g in range(m.ngeom) if m.geom_bodyid[g] in feet
                   and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_SPHERE]
        if not contact:
            raise ValueError('Standing model has no contracted spherical foot contacts')
        sole = min(d.geom_xpos[g, 2] - m.geom_size[g, 0] for g in contact)
        measured = d.qpos.copy()
        # Solve the root height in the *local* model. Never move the live robot or
        # mutate compiled geometry caches. The offset estimates its support plane.
        radius = max(float(m.geom_size[g, 0]) for g in contact)
        centre = measured[2] - sole
        lo, hi = centre - 2 * radius, centre + 2 * radius
        for _ in range(50):
            d.qpos[:] = measured
            d.qpos[2] = (lo + hi) / 2
            d.qvel[:] = d.qacc[:] = 0
            mujoco.mj_inverse(m, d)
            if d.qfrc_inverse[2] > 0:
                hi = d.qpos[2]
            else:
                lo = d.qpos[2]
        self.height_offset = state.base_height - float(d.qpos[2])
        root_wrench = d.qfrc_inverse[:6].copy()
        self.reference = d.qpos.copy()
        kp = np.zeros(m.nu)
        kp[self.act] = policy.kp
        if not np.isfinite(kp).all() or np.any(kp <= 0):
            raise ValueError('Standing feedback requires positive finite motor stiffness')
        torque = np.zeros(m.nu)
        torque[self.act] = d.qfrc_inverse[self.dadr]
        self.target0 = self.reference[m.jnt_qposadr[m.actuator_trnid[:, 0]]] + torque / kp
        d.ctrl[:] = torque
        mujoco.mj_forward(m, d)
        A = np.zeros((2 * m.nv, 2 * m.nv))
        B = np.zeros((2 * m.nv, m.nu))
        mujoco.mjd_transitionFD(m, d, float(settings['derivative_epsilon']), True, A, B, None, None)
        com = np.zeros((3, m.nv))
        mujoco.mj_jacSubtreeCom(m, d, com, int(m.jnt_bodyid[free[0]]))
        support = np.zeros_like(com)
        for foot in feet:
            jac = np.zeros_like(com)
            mujoco.mj_jacBodyCom(m, d, jac, None, foot)
            support += jac / len(feet)
        relative = com - support
        Q = np.zeros_like(A)
        Q[:m.nv, :m.nv] = float(settings['balance_cost']) * relative.T @ relative
        weights = settings['posture_cost']
        for i, name in enumerate(policy.joint_names):
            Q[self.dadr[i], self.dadr[i]] += next(
                (float(v) for key, v in weights.items() if key != 'default' and key in name),
                float(weights['default']))
        selection = np.zeros((m.nu, 2 * m.nv))
        selection[np.arange(m.nu), m.jnt_dofadr[m.actuator_trnid[:, 0]]] = 1
        # Compose the implicit inner PD and the declared command hold interval.
        Ap, Bp = A - B @ np.diag(kp) @ selection, B @ np.diag(kp)
        A = np.linalg.matrix_power(Ap, decimation)
        B = sum(np.linalg.matrix_power(Ap, i) @ Bp for i in range(decimation))
        R, P = np.diag(kp ** 2), Q.copy()
        steps = math.ceil(float(settings['horizon_s']) / policy.policy_dt)
        previous_gain = None
        for _ in range(steps):
            if time.monotonic() - started > float(settings['design_timeout_s']):
                raise TimeoutError('Standing controller design exceeded its wall deadline')
            gain = np.linalg.solve(R + B.T @ P @ B, B.T @ P @ A)
            following = Q + A.T @ P @ A - A.T @ P @ B @ gain
            following = (following + following.T) / 2
            residual = float(np.max(abs(following - P)))
            change = float(np.max(abs(gain - previous_gain))) if previous_gain is not None else None
            previous_gain, P = gain, following
        self.gain = np.linalg.solve(R + B.T @ P @ B, B.T @ P @ A)
        if not np.isfinite(self.gain).all() or not np.isfinite(self.target0).all():
            raise ValueError('Non-finite standing feedback')
        self.delta = np.zeros(m.nv)
        self.metadata = dict(design_seconds=time.monotonic() - started, horizon_steps=steps,
                             gain_change=change, riccati_residual=residual,
                             root_wrench=root_wrench.tolist(), support_height=self.height_offset,
                             reference_sim_time=state.t, physics_dt_s=dt,
                             release_physics_dt_s=release_dt, command_decimation=decimation)

    def _read(self, state):
        count = len(self.policy.joint_names)
        values = [state.t, state.base_height, *state.odom_xy, *state.imu_quat,
                  *state.imu_gyro, *state.joint_pos, *state.joint_vel]
        if (len(state.odom_xy) != 2 or len(state.imu_quat) != 4 or len(state.imu_gyro) != 3
                or len(state.joint_pos) != count or len(state.joint_vel) != count
                or not np.isfinite(values).all()
                or not math.isclose(np.linalg.norm(state.imu_quat), 1, abs_tol=1e-3)):
            raise ValueError('Standing feedback requires complete finite robot sensors')
        d = self.data
        d.qpos[:2] = np.asarray(state.odom_xy) - self.origin
        d.qpos[2] = state.base_height - self.height_offset
        d.qpos[3:7] = state.imu_quat
        d.qpos[self.qadr] = state.joint_pos
        d.qvel[3:6] = state.imu_gyro
        d.qvel[self.dadr] = state.joint_vel
        position = np.array([*state.odom_xy, state.base_height])
        d.qvel[:3] = 0
        if self.previous is not None:
            t, old = self.previous
            if state.t <= t:
                raise ValueError('Standing feedback requires an advancing sensor clock')
            d.qvel[:3] = (position - old) / (state.t - t)
        self.previous = (state.t, position)

    def targets(self, state):
        import mujoco
        self._read(state)
        mujoco.mj_differentiatePos(self.model, self.delta, 1, self.reference, self.data.qpos)
        targets = (self.target0 - self.gain @ np.r_[self.delta, self.data.qvel])[self.act]
        if not np.isfinite(targets).all():
            raise ValueError('Standing feedback returned non-finite targets')
        return targets


class StandingBalance:
    """Build away from the motor loop and discard results from revoked generations."""
    def __init__(self, assets_root, policy, settings, physics_dt=None):
        root = Path(assets_root).resolve()
        path = (root / settings['model']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Standing model must be a file in the active asset root')
        self.path, self.policy, self.settings = path, policy, settings
        self.physics_dt = physics_dt
        self.lock = threading.Lock()
        self.generation = 0
        self.controller = self.worker = None
        self.error = ''
        self.closed = False
        self.failed_generation = None

    def reset(self):
        with self.lock:
            self.generation += 1
            self.controller = None
            self.error = ''

    def update(self, state, *, quiet):
        with self.lock:
            if self.closed:
                return None
            controller = self.controller
            if controller is None and quiet and self.worker is None and self.failed_generation != self.generation:
                generation = self.generation
                self.worker = threading.Thread(target=self._design,
                    args=(copy.deepcopy(state), generation), name='nerv-standing-design', daemon=True)
                self.worker.start()
        if controller is None:
            return None
        try:
            return controller.targets(state)
        except Exception as error:
            with self.lock:
                self.controller = None
                self.error = f'{type(error).__name__}: {error}'
                self.failed_generation = self.generation
            return None

    def _design(self, state, generation):
        try:
            result = LocalStandingController(self.path, self.policy, state, self.settings, self.physics_dt)
            error = ''
        except Exception as exc:
            result, error = None, f'{type(exc).__name__}: {exc}'
        with self.lock:
            if not self.closed and generation == self.generation:
                self.controller, self.error = result, error
                if error:
                    self.failed_generation = generation
            self.worker = None

    def status(self):
        with self.lock:
            return dict(mode='standing' if self.controller is not None else 'gait',
                        preparing=self.worker is not None, error=self.error,
                        generation=self.generation,
                        design=self.controller.metadata if self.controller is not None else None)

    def close(self):
        with self.lock:
            self.closed = True
            self.generation += 1
            self.controller = None
            worker = self.worker
        if worker is not None:
            worker.join(timeout=float(self.settings['design_timeout_s']) + 1)
