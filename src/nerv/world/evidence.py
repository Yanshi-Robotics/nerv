"""Operator-only physics-step evidence. Never exported through robot sensors."""
from collections import deque
import math
import time


class PhysicsEvidence:
    def __init__(self, *, sample_hz, duration_s, max_samples):
        if (not math.isfinite(sample_hz) or sample_hz < 20 or not math.isfinite(duration_s)
                or duration_s <= 0 or type(max_samples) is not int or max_samples < 1):
            raise ValueError('Specify a finite rate of at least 20 Hz and bounded recording limits')
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
            self.samples.append(dict(sequence=self.sequence, sim_time=now, epoch=owner.epoch,
                world_instance_id=owner.world_instance_id, topology_revision=owner.topology_revision,
                physics_steps=self.steps, robot_collision_seen=self.collision_seen, fall_seen=self.fall_seen,
                events=self.pending_events, robots={rid:v.operator_geometry() for rid,v in owner.views.items()}))
            self.pending_events = []
            self.next_sample += self.period
        if now - self.first_time >= self.duration or time.monotonic() - self.wall_started >= self.duration * 2:
            self.finished = True

    def read(self, after=0):
        return dict(schema=1, samples=[s for s in self.samples if s['sequence'] > after],
                    finished=self.finished, dropped_samples=self.dropped,
                    physics_steps=self.steps, latest_sequence=self.sequence,
                    pending_events=list(self.pending_events), physics_events_complete=self.error is None,
                    error=self.error, robot_collision_seen=self.collision_seen, fall_seen=self.fall_seen)
