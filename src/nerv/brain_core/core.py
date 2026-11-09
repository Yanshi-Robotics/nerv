"""One authenticated Agent binding per Run. Existing journals own all task state."""
from __future__ import annotations

import hashlib
import threading
import time
import uuid

from ..platform.agent_gateway import AgentGateway, OPERATIONS, _object

OPERATIONS.setdefault("brain_get", ("Read this Brain binding, identity and recovery state.", _object()))

IDENTITY_FIELDS = ("brain_instance_id", "run_id", "robot_instance_id", "world_instance_id", "binding_generation")


class BrainCore(AgentGateway):
    def __init__(self, hub, projects, root=None):
        super().__init__(hub, projects, root)
        self._binding_lock = threading.RLock()

    def _bindings(self):
        for project in self.projects.store.list():
            for run in self.projects.store.runs(project['id']):
                if run.get('brain_instance_id'):
                    yield project, run

    def reserve(self, project_id, client_id):
        """Operator-only; reserve identity without creating devices or motion authority."""
        import json
        with self._binding_lock, self.projects._lock:
            record = json.loads(self._path(client_id).read_text())
            if record.get('revoked') or project_id not in record['projects']:
                raise ValueError('Select a valid Agent connection for this project')
            project = self.projects.store.get(project_id)
            run = self.projects.active_run(project_id)
            if run and run.get('client_id') not in (None, client_id):
                raise ValueError('End the Run before replacing its Agent binding')
            if run and run.get('brain_instance_id'):
                return self.describe(run)
            if run is None:
                run = {'id': 'r_' + uuid.uuid4().hex, 'project_id': project_id,
                       'project_revision': project['revision'], 'snapshot': project,
                       'status': 'reserved', 'session_id': '', 'created_at': time.time()}
            run.update(brain_instance_id='brain_' + uuid.uuid4().hex,
                       robot_instance_id='robot_' + uuid.uuid4().hex,
                       world_instance_id=project.get('world_instance_id') or 'world_' + uuid.uuid4().hex,
                       binding_generation=1, client_id=client_id)
            self.projects.store.save_run(project_id, run)
            return self.describe(run)

    def describe(self, run):
        identity = {key: run.get(key) for key in IDENTITY_FIELDS}
        identity['run_id'] = run['id']
        return {**identity, 'project_id': run['project_id'], 'client_id': run.get('client_id'),
                'status': run['status'], 'control': self.control(run['project_id']),
                'recovery': 'Read current state and fresh observations; motion never resumes automatically.'}

    def resolve_binding(self, client_id, brain_id=None):
        matches = [(project, run) for project, run in self._bindings()
                   if run.get('client_id') == client_id
                   and (run.get('brain_instance_id') == brain_id if brain_id else
                        run['status'] in {'reserved', 'starting', 'running', 'stopping', 'stop_unconfirmed'})]
        if len(matches) != 1:
            raise ValueError('Select one operator-bound Brain instance with --brain')
        return matches[0]

    def set_control(self, project_id, *, accepting, owner_id=None):
        if accepting:
            run = self.projects.active_run(project_id)
            if not run or run.get('client_id') != owner_id:
                raise ValueError('Control must belong to the Agent fixed to this Run')
        return super().set_control(project_id, accepting=accepting, owner_id=owner_id)

    def invoke(self, token, operation, args, brain_id=None):
        record = self._authenticate(token)
        from jsonschema import validate
        if operation not in OPERATIONS:
            raise ValueError("Unknown external Agent operation")
        validate(args, OPERATIONS[operation][1])
        project, run = self.resolve_binding(record['id'], brain_id)
        identity = self.describe(run)
        if operation == 'brain_get':
            if args:
                raise ValueError('brain_get takes no arguments')
            return identity
        if operation == 'projects_list':
            if args:
                raise ValueError('projects_list takes no arguments')
            return {'projects': [self.projects.get(project['id'])], **identity}
        args = dict(args)
        if args.get('project_id', project['id']) != project['id']:
            raise PermissionError('Project is outside this Brain binding')
        args['project_id'] = project['id']
        active = self.projects.active_run(project['id'])
        if not active or active['id'] != run['id']:
            if operation not in {'project_get', 'events', 'task_status'}:
                raise ValueError('This Brain Run has ended; no request will be replayed')
            if operation == 'project_get':
                return {'project': project, **identity}
            if not run.get('session_id'):
                return {'task': None, 'events': [], **identity}
            if operation == 'task_status':
                return {**self.hub.tasks.snapshot(run['session_id']), **identity}
            return {**self._events(record, project['id'], run['session_id'], args), **identity}
        # Namespace retries by immutable Brain, including after restarts.
        if 'request_id' in args:
            args['request_id'] = hashlib.sha256((run['brain_instance_id'] + ':' + args['request_id']).encode()).hexdigest()
        result = super().invoke(token, operation, args)
        current = next(r for r in self.projects.store.runs(project['id']) if r['id'] == run['id'])
        envelope = {key: self.describe(current)[key] for key in IDENTITY_FIELDS}
        envelope['control_generation'] = self.control(project['id']).get('control_generation', 0)
        if 'events' in result:
            result['events'] = [{**event, **envelope} for event in result['events']]
        return {**result, **envelope}
