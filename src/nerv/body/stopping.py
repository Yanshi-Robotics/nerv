"""Sensor-based continuous stop evidence, independent of request acknowledgements."""
import math
import threading
import time


class QuietWindow:
    def __init__(self, *, joint_limit, linear_limit, angular_limit, duration, max_age):
        self.joint_limit, self.linear_limit, self.angular_limit = joint_limit, linear_limit, angular_limit
        self.duration, self.max_age = duration, max_age
        self.lock = threading.RLock()
        self.previous = None
        self.since = None
        self.updated = 0.0
        self.confirmed = False
        self.revision = 0

    def invalidate(self):
        with self.lock:
            self.previous = self.since = None
            self.updated = 0.0
            self.confirmed = False
            self.revision += 1

    def update(self, state, *, fallen=False):
        with self.lock:
            try:
                t = float(state.t)
                position = [*state.odom_xy, state.base_height]
                joints, gyro = list(state.joint_vel), list(state.imu_gyro)
                valid = (len(position) == 3 and bool(joints) and len(gyro) == 3
                         and all(math.isfinite(float(v)) for v in [t, *position, *joints, *gyro]))
            except (AttributeError, TypeError, ValueError):
                valid = False
            quiet = False
            if valid:
                if self.previous is not None:
                    old_t, old_position = self.previous
                    if t == old_t:
                        if (fallen or position != old_position
                                or math.hypot(*gyro) > self.angular_limit
                                or max(map(abs, joints)) > self.joint_limit):
                            if self.confirmed:
                                self.revision += 1
                            self.confirmed = False
                            self.since = None
                        return self.snapshot()
                    dt = t - old_t
                    if 0 < dt <= self.max_age:
                        linear = math.dist(position, old_position) / dt
                        quiet = (not fallen and linear <= self.linear_limit
                                 and math.hypot(*gyro) <= self.angular_limit
                                 and max(map(abs, joints)) <= self.joint_limit)
                self.previous = (t, position)
                self.updated = time.monotonic()
            else:
                self.previous = None
            self.since = (t if self.since is None else self.since) if quiet else None
            confirmed = bool(quiet and t - self.since >= self.duration)
            if confirmed != self.confirmed:
                self.revision += 1
            self.confirmed = confirmed
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            confirmed = self.confirmed and time.monotonic() - self.updated <= self.max_age
            return dict(stop_confirmed=confirmed, stop_state='stopped' if confirmed else 'stop_unconfirmed',
                        stop_evidence_revision=self.revision,
                        basis='joint_velocity_and_base_odometry',
                        observed_sim_time=self.previous[0] if self.previous else None,
                        stable_seconds=(self.previous[0] - self.since) if self.since is not None else 0.0)
