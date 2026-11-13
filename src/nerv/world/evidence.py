"""Operator-only physics-step evidence. Never exported through robot sensors."""
from collections import deque
import math
import time


class PhysicsEvidence:
    def __init__(self, *, sample_hz, duration_s, max_samples, obstacle_margin_m=None):
        if (not math.isfinite(sample_hz) or sample_hz < 20 or not math.isfinite(duration_s)
                or duration_s <= 0 or type(max_samples) is not int or max_samples < 1):
            raise ValueError('Specify a finite rate of at least 20 Hz and bounded recording limits')
        if obstacle_margin_m is not None and (type(obstacle_margin_m) not in (int, float)
                or not math.isfinite(obstacle_margin_m) or obstacle_margin_m < 0):
            raise ValueError('Obstacle evidence margin must be finite and nonnegative')
        self.obstacle_margin = obstacle_margin_m
        self.period, self.duration = 1 / sample_hz, duration_s
        self.samples = deque(maxlen=max_samples)
        self.sequence = self.dropped = self.steps = 0
        self.next_sample = self.first_time = None
        self.wall_started = time.monotonic()
        self.finished = False
        self.error = None
        self.epoch = self.topology = None
        self.collision_seen = self.fall_seen = False
        self.pending_events = []
        self.last_contacts = set()
        self.last_falls = set()
        self.dynamic_geometry_ids = None

    def step(self, owner):
        if self.finished:
            return
        now = float(owner.data.time)
        if self.first_time is None:
            self.first_time, self.next_sample = now, now
            self.epoch, self.topology = owner.epoch, owner.topology_revision
        if owner.epoch != self.epoch or owner.topology_revision != self.topology:
            self.finished = True
            raise ValueError('Recording topology changed; start new evidence after the transaction')
        self.steps += 1
        roots = {v.base_body: rid for rid, v in owner.views.items()}
        if self.dynamic_geometry_ids is None:
            model = owner.model
            movable_roots = {int(model.body_rootid[b]) for b in range(model.nbody)
                             if model.body_dofnum[b] or model.body_mocapid[b] >= 0} - set(roots)
            self.dynamic_geometry_ids = [g for g in range(model.ngeom)
                if int(model.body_rootid[model.geom_bodyid[g]]) in movable_roots
                and (model.geom_contype[g] or model.geom_conaffinity[g])]
        contacts = set()
        for contact in owner.data.contact:
            pair = [roots.get(int(owner.model.body_rootid[owner.model.geom_bodyid[g]])) for g in contact.geom]
            if all(pair) and pair[0] != pair[1] and contact.dist <= 0:
                contacts.add(tuple(sorted(pair)))
        falls = {rid for rid, view in owner.views.items() if view.status().get('fallen') is True}
        for pair in contacts - self.last_contacts:
            self.pending_events.append(dict(kind='robot_contact', robots=list(pair), sim_time=now, physics_step=self.steps))
        for rid in falls - self.last_falls:
            self.pending_events.append(dict(kind='fall', robot_instance_id=rid, sim_time=now, physics_step=self.steps))
        self.last_contacts, self.last_falls = contacts, falls
        self.collision_seen |= bool(contacts)
        self.fall_seen |= bool(falls)
        if now + 1e-9 >= self.next_sample:
            self.sequence += 1
            if len(self.samples) == self.samples.maxlen:
                self.dropped += 1
            moving = owner.geometry_boxes(self.dynamic_geometry_ids)
            dynamic_obstacles = [dict(box, geom_id=gid) for gid, box in zip(self.dynamic_geometry_ids, moving)]
            robots = {rid:v.operator_geometry() for rid,v in owner.views.items()}
            if self.obstacle_margin is not None:
                # Check ALL dynamic geometry, retain only possible intersections
                # with expanded robot AABBs. This is an exact broad-phase filter
                # for the declared scoring margin, not a distance heuristic.
                if any(not all(math.isfinite(x) for point in box['bounds'] for x in point)
                       for box in dynamic_obstacles):
                    raise ValueError('Non-finite dynamic obstacle geometry')
                dynamic_obstacles = [box for box in dynamic_obstacles if any(
                    all(robot['bounds'][0][i] - self.obstacle_margin < box['bounds'][1][i]
                        and robot['bounds'][1][i] + self.obstacle_margin > box['bounds'][0][i]
                        for i in range(3)) for robot in robots.values())]
            self.samples.append(dict(sequence=self.sequence, sim_time=now, epoch=owner.epoch,
                world_instance_id=owner.world_instance_id, topology_revision=owner.topology_revision,
                physics_steps=self.steps, robot_collision_seen=self.collision_seen, fall_seen=self.fall_seen,
                events=self.pending_events, dynamic_obstacles=dynamic_obstacles,
                dynamic_geometry_checked=len(moving), robots=robots))
            self.pending_events = []
            self.next_sample += self.period
        if now - self.first_time >= self.duration or time.monotonic() - self.wall_started >= self.duration * 2:
            self.finished = True

    def read(self, after=0):
        return dict(schema=2, samples=[s for s in self.samples if s['sequence'] > after],
                    dynamic_geometry_ids=self.dynamic_geometry_ids,
                    dynamic_obstacle_margin_m=self.obstacle_margin,
                    finished=self.finished, dropped_samples=self.dropped,
                    physics_steps=self.steps, latest_sequence=self.sequence,
                    pending_events=list(self.pending_events), physics_events_complete=self.error is None,
                    error=self.error, robot_collision_seen=self.collision_seen, fall_seen=self.fall_seen)
