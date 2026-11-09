"""One authenticated Agent binding per Run. Existing journals own all task state."""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import threading
import time
import uuid

from ..platform.agent_gateway import AgentGateway, OPERATIONS, _object

OPERATIONS.setdefault("brain_get", ("Read this Brain binding, identity and recovery state.", _object()))

_request_run = ContextVar("nerv_brain_request_run", default=None)

IDENTITY_FIELDS = ("brain_instance_id", "run_id", "robot_instance_id", "world_instance_id", "binding_generation", "world_generation", "world_epoch", "robot_epoch")


class BrainCore(AgentGateway):
    def __init__(self, hub, projects, root=None):
        self._transports = {}
        self._binding_lock = threading.RLock()
        super().__init__(hub, projects, root)

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
                       binding_generation=1, world_generation=1, client_id=client_id)
            self.projects.store.save_run(project_id, run)
            return self.describe(run)

    def describe(self, run):
        identity = {key: run.get(key) for key in IDENTITY_FIELDS}
        identity['run_id'] = run['id']
        active = self.projects.active_run(run['project_id'])
        control = self.control(run['project_id']) if active and active['id'] == run['id'] else {'accepting':False, 'owner_id':None}
        return {**identity, 'project_id': run['project_id'], 'client_id': run.get('client_id'),
                'status': run['status'], 'control': control,
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

    def transport(self, token, action, brain_id=None, transport_id=None):
        """Transport liveness is separate from observing or acknowledging images."""
        from .. import config
        record = self._authenticate(token)
        project, run = self.resolve_binding(record['id'], brain_id)
        bid = run['brain_instance_id']
        with self._binding_lock:
            previous = self._transports.get(bid)
            if action == 'connect':
                if previous and time.monotonic() < previous['deadline']:
                    raise ValueError('This Brain already has a live MCP connection')
                tid = uuid.uuid4().hex
                self._transports[bid] = {'id': tid, 'project_id': project['id'], 'run_id':run['id'],
                                         'deadline': time.monotonic() + config.NODE_LIVENESS_TIMEOUT}
            elif action in {'heartbeat', 'disconnect'}:
                if not previous or previous['id'] != transport_id:
                    raise ValueError('MCP connection generation changed; reconnect read-only')
                tid = previous['id']
                if action == 'heartbeat':
                    if time.monotonic() >= previous['deadline']:
                        raise ValueError('MCP connection expired; reconnect read-only')
                    previous['deadline'] = time.monotonic() + config.NODE_LIVENESS_TIMEOUT
                else:
                    self._transports.pop(bid, None)
            else:
                raise ValueError('Unknown transport operation')
        if action in {'connect', 'disconnect'}:
            # A fresh transport has no inherited movement authority. Stop only its
            # active Run, never a replacement Run or another robot.
            with self._lock:
                self._invalidate_control(project['id'])
            active = self.projects.active_run(project['id'])
            if active and active['id'] == run['id'] and active.get('session_id'):
                self.hub.tasks.cancel(active['session_id'], reason='MCP transport ' + action)
        return {'transport_id': tid, 'brain_instance_id': bid,
                'heartbeat_s': config.NODE_LIVENESS_TIMEOUT / 3, 'motion_authorized': False if action != 'heartbeat' else None}

    def check_expired(self):
        # Called by the independent Host watchdog, including when no tools are used.
        super().check_expired()
        for pid in self.projects.worlds.check_health():
            with self._lock:
                self._invalidate_control(pid)
        with self._binding_lock:
            expired = [(bid, value) for bid, value in self._transports.items()
                       if time.monotonic() >= value['deadline']]
            for bid, _ in expired:
                self._transports.pop(bid, None)
        def stop_expired(item):
            _, value = item
            pid = value['project_id']
            with self._lock:
                self._invalidate_control(pid)
            run = self.projects.active_run(pid)
            if run and run['id'] == value['run_id'] and run.get('session_id'):
                self.hub.tasks.cancel(run['session_id'], reason='MCP transport lost')
        if expired:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=len(expired)) as pool:
                list(pool.map(stop_expired, expired))

    def invoke(self, token, operation, args, brain_id=None, transport_id=None):
        record = self._authenticate(token)
        from jsonschema import validate
        if operation not in OPERATIONS:
            raise ValueError("Unknown external Agent operation")
        validate(args, OPERATIONS[operation][1])
        project, run = self.resolve_binding(record['id'], brain_id)
        with self._binding_lock:
            transport = self._transports.get(run['brain_instance_id'])
            if transport and (transport['id'] != transport_id or time.monotonic() >= transport['deadline']):
                raise PermissionError('MCP connection generation expired or changed')
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
        context = _request_run.set(run["id"])
        try:
            return super().invoke(token, operation, args)
        finally:
            _request_run.reset(context)

    def _dispatch(self, record, operation, args):
        # The complete receipt is persisted by the idempotency layer. A retry
        # keeps the original generation rather than relabelling old execution.
        run = self.projects.active_run(args['project_id'])
        if run is None or run.get('client_id') != record['id'] or run['id'] != _request_run.get():
            raise ValueError('Brain Run changed before dispatch')
        captured = {key: self.describe(run)[key] for key in IDENTITY_FIELDS}
        captured['control_generation'] = self.control(args['project_id']).get('control_generation', 0)
        result = super()._dispatch(record, operation, args)
        if operation == 'run_start':
            current = self.projects.active_run(args['project_id'])
            captured.update({key:self.describe(current)[key] for key in IDENTITY_FIELDS})
        if 'events' in result:
            result['events'] = [{**captured, **event} for event in result['events']]
        return {**result, **captured}
