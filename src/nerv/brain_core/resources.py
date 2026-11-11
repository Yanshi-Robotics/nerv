"""Durable robot/Brain identities; execution history stays in Project Run journals."""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from filelock import FileLock
from ..platform.projects import _id, _write


class RobotResources:
    def __init__(self, projects):
        self.projects = projects
        self.root = projects.store.root.parent / 'robot-instances'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = FileLock(str(self.root / '.lock'))
        # Import identities, not motion authorization. Historical Run records are
        # immutable inputs; their IDs are not rewritten to the canonical Brain.
        with self.lock:
            for resource in self.list():
                if resource['status'] in {'ready', 'preparing'}:
                    self.save(dict(resource, status='offline', reason='Host restarted; attachment requires recovery'))
            for project in projects.store.list():
                if self.for_project(project['id']) is not None:
                    continue
                runs = projects.store.runs(project['id'])
                run = next((r for r in runs if r.get('brain_instance_id')), None)
                if run:
                    self.save({key: run.get(key) for key in (
                        'project_id', 'brain_instance_id', 'robot_instance_id',
                        'world_instance_id', 'binding_generation', 'client_id')}
                        | {'mode': project['mode'], 'created_at': run['created_at'],
                           'status': 'offline', 'shared_world': bool(run.get('shared_world'))})

    def list(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('robot_*.json'))]

    def get(self, robot_id):
        return json.loads((self.root / (_id(robot_id) + '.json')).read_text())

    def save(self, value):
        _write(self.root / (_id(value['robot_instance_id']) + '.json'), value)
        return value

    def for_project(self, project_id):
        return next((r for r in self.list() if r['project_id'] == project_id and r['status'] != 'removed'), None)

    def reserve(self, project, client_id):
        with self.lock:
            value = self.for_project(project['id'])
            if value is None:
                value = dict(project_id=project['id'], mode=project['mode'],
                             robot_instance_id='robot_' + uuid.uuid4().hex,
                             brain_instance_id='brain_' + uuid.uuid4().hex,
                             world_instance_id='world_' + uuid.uuid4().hex,
                             binding_generation=1, client_id=client_id,
                             status='allocated', shared_world=False, created_at=time.time())
            elif value['client_id'] != client_id:
                active = self.projects.active_run(project['id'])
                if active:
                    raise ValueError('End the Run before replacing its Agent binding')
                value.update(client_id=client_id, binding_generation=value['binding_generation'] + 1)
            return self.save(value)

    def bind_world(self, project_id, world_id):
        with self.lock:
            value = self.for_project(project_id)
            if value is None:
                raise ValueError('Allocate a robot before binding a World')
            value.update(world_instance_id=world_id, shared_world=True)
            return self.save(value)
