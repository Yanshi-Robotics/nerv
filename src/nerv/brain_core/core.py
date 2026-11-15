"""Persistent robot/Brain identity with bounded execution in existing Run journals."""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import threading
import time
import uuid

from ..platform.agent_gateway import AgentGateway, OPERATIONS, _object, IDENT
from .discovery import Discovery, DISCOVERY_OPERATIONS

OPERATIONS.setdefault("brain_get", ("Read this Brain binding, identity and recovery state.", _object()))
OPERATIONS.setdefault('control_acquire', ('Request control under an existing operator grant after observing.',
    _object({'project_id':IDENT, 'receipt_id':IDENT}, ['project_id','receipt_id'])))
OPERATIONS.setdefault('control_release', ('Release owned control and stop execution.',
    _object({'project_id':IDENT}, ['project_id'])))

_request_run = ContextVar("nerv_brain_request_run", default=None)

IDENTITY_FIELDS = ("brain_instance_id", "run_id", "robot_instance_id", "world_instance_id", "binding_generation", "world_generation", "world_epoch", "robot_epoch")


class BrainCore(AgentGateway):
    def __init__(self, hub, projects, root=None):
        self._transports = {}
        self._binding_lock = threading.RLock()
        self._robot_control_locks = {}
        from .resources import RobotResources
        self.resources = RobotResources(projects)
        projects.resources = self.resources
        projects.brain_core = self
        super().__init__(hub, projects, root)
        self.discovery = Discovery(self)
        from .attachments import RuntimeAttachments
        self.attachments = RuntimeAttachments(self)
        projects.attachments = self.attachments

    def shutdown(self):
        self.discovery.close()
        super().shutdown()

    def create(self, name, projects, permissions=None, world_grants=None):
        grants = self.discovery.validate_grants(world_grants or [])
        result = super().create(name, projects, permissions, allow_empty=bool(grants))
        if grants:
            import json
            from ..platform.projects import _write
            path = self._path(result['connection']['id'])
            record = json.loads(path.read_text())
            record['world_grants'] = grants
            _write(path, record)
        return result

    def _bindings(self):
        for resource in self.resources.list():
            if resource['status'] == 'removed':
                continue
            project = self.projects.store.get(resource['project_id'])
            runs = self.projects.store.runs(project['id'])
            run = next((r for r in runs if r.get('brain_instance_id') == resource['brain_instance_id']), None)
            if run is None:
                run = dict(resource, id=None, snapshot=project, status=resource['status'],
                           project_revision=project['revision'], world_generation=(self.projects.worlds.get(resource['world_instance_id'])['generation'] if resource['shared_world'] else 1), session_id='')
            yield project, run

    def reserve(self, project_id, client_id):
        """Operator-only; reserve identity without creating devices or motion authority."""
        import json
        with self.projects.worlds.lock, self._binding_lock, self.projects._lock:
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
            resource = self.resources.reserve(project, client_id)
            run.update({key: resource[key] for key in (
                'brain_instance_id', 'robot_instance_id', 'world_instance_id',
                'binding_generation', 'client_id', 'shared_world')})
            if resource['shared_world']:
                world = self.projects.worlds.get(resource['world_instance_id'])
                run['world_generation'] = world['generation']
                for slot in world['roster']:
                    if slot['robot_instance_id'] == resource['robot_instance_id']:
                        slot['run_id'] = run['id']
                self.projects.worlds.save(world)
            else:
                run['world_generation'] = 1
            self.projects.store.save_run(project_id, run)
            return self.describe(run)

    def describe(self, run):
        identity = {key: run.get(key) for key in IDENTITY_FIELDS}
        identity['run_id'] = run['id']
        active = self.projects.active_run(run['project_id'])
        control = self.control(run['project_id']) if active and active['id'] == run['id'] else {'accepting':False, 'owner_id':None}
        resource = self.resources.for_project(run['project_id']) or {}
        world = self.projects.worlds.get(resource['world_instance_id']) if resource.get('shared_world') else {}
        return {**identity, 'project_id': run['project_id'], 'client_id': run.get('client_id'),
                'status': run['status'], 'control': control,
                'resource_status': resource.get('status', 'offline'),
                'world_health': world.get('health', {'state':'not_observed'}),
                'recovery': 'Read current state and fresh observations; motion never resumes automatically.'}

    def resolve_binding(self, client_id, brain_id=None):
        import json
        record = json.loads(self._path(client_id).read_text())
        matches = [(project, run) for project, run in self._bindings()
                   if project['id'] in record['projects']
                   and (run.get('brain_instance_id') == brain_id if brain_id else True)]
        if len(matches) != 1:
            raise ValueError('Select one operator-bound Brain instance with --brain')
        return matches[0]

    def set_control(self, project_id, *, accepting, owner_id=None):
        with self._robot_control_locks.setdefault(project_id, threading.RLock()):
            if accepting and not self._world_motion_available(project_id):
                raise ValueError('World health is unconfirmed; obtain fresh state after recovery')
            return super().set_control(project_id, accepting=accepting, owner_id=owner_id)

    def _world_motion_available(self, project_id):
        resource = self.resources.for_project(project_id)
        if not resource or not resource.get('shared_world'):
            return True
        world = self.projects.worlds.get(resource['world_instance_id'])
        return world['status'] == 'ready' and world.get('health', {}).get('state') != 'unknown'

    def _owns(self, record, project_id, *, allow_paused=False):
        super()._owns(record, project_id, allow_paused=allow_paused)
        if not allow_paused and not self._world_motion_available(project_id):
            raise PermissionError('World health is unconfirmed; motion remains disabled')

    def _still_current(self, record, project_id, lease):
        return self._world_motion_available(project_id) and super()._still_current(record, project_id, lease)

    def transport(self, token, action, brain_id=None, transport_id=None):
        """Transport liveness is separate from observing or acknowledging images."""
        from .. import config
        record = self._authenticate(token)
        if brain_id is None and record.get('world_grants'):
            if action not in {'connect', 'heartbeat', 'disconnect'}:
                raise ValueError('Unknown transport operation')
            return {'transport_id':'bootstrap', 'brain_instance_id':None,
                    'heartbeat_s':config.NODE_LIVENESS_TIMEOUT / 3, 'motion_authorized':False}
        project, run = self.resolve_binding(record['id'], brain_id)
        bid = run['brain_instance_id']
        transport_key = bid + ':' + record['id']
        with self._binding_lock:
            previous = self._transports.get(transport_key)
            if action == 'connect':
                if previous and time.monotonic() < previous['deadline']:
                    raise ValueError('This Brain already has a live MCP connection')
                tid = uuid.uuid4().hex
                self._transports[transport_key] = {'id': tid, 'project_id': project['id'], 'run_id':run['id'], 'client_id':record['id'],
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
                    self._transports.pop(transport_key, None)
            else:
                raise ValueError('Unknown transport operation')
        if action in {'connect', 'disconnect'} and self.control(project['id']).get('owner_id') == record['id']:
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
        self.projects.worlds.check_health()
        with self._binding_lock:
            expired = [(bid, value) for bid, value in self._transports.items()
                       if time.monotonic() >= value['deadline']]
            for bid, _ in expired:
                self._transports.pop(bid, None)
        def stop_expired(item):
            _, value = item
            pid = value['project_id']
            if self.control(pid).get('owner_id') != value['client_id']:
                return
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
        if operation in DISCOVERY_OPERATIONS:
            validate(args, DISCOVERY_OPERATIONS[operation][1])
            if brain_id is not None:
                raise PermissionError('Resource management requires the unbound bootstrap connection')
            return self.discovery.invoke(record, operation, args)
        if operation not in OPERATIONS:
            raise ValueError("Unknown external Agent operation")
        validate(args, OPERATIONS[operation][1])
        project, run = self.resolve_binding(record['id'], brain_id)
        with self._binding_lock:
            transport = self._transports.get(run['brain_instance_id'] + ':' + record['id'])
            if ((transport_id is not None and transport is None)
                    or (transport and (transport['id'] != transport_id or time.monotonic() >= transport['deadline']))):
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
        if operation in {'control_acquire', 'control_release'}:
            return self._agent_control(record, project['id'], operation, args)
        active = self.projects.active_run(project['id'])
        if (not active or active['id'] != run['id']) and operation != 'run_start':
            if operation not in {'project_get', 'events', 'task_status', 'capabilities', 'observe'}:
                raise ValueError('This Brain Run has ended; no request will be replayed')
            if operation == 'project_get':
                return {'project': project, **identity}
            if operation in {'capabilities', 'observe'}:
                return self._read_resource(project['id'], operation)
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

    def _read_resource(self, project_id, operation):
        """Sensor access without a Run; never creates an authorization receipt."""
        import base64
        resource = self.resources.for_project(project_id)
        if not resource:
            raise ValueError('Robot resource is unavailable')
        identity = {key:resource.get(key) for key in IDENTITY_FIELDS}
        identity.update(run_id=None, project_id=project_id, motion_authorized=False,
                        resource_status=resource['status'], robot_epoch=resource.get('runtime_epoch'),
                        runtime_generation=resource.get('runtime_generation', 0))
        world = self.projects.worlds.get(resource['world_instance_id']) if resource.get('shared_world') else None
        if world:
            identity.update(world_generation=world['generation'], world_epoch=world.get('epoch'),
                            topology_revision=world.get('topology_revision'))
        if resource['status'] != 'ready' or (world and world['status'] != 'ready'):
            return {**identity, 'connected':False, 'actions':[], 'prepare_state':resource['status']}
        robot = self.hub.body_client(resource['robot_instance_id'])
        before = (robot.health() or {}).get('epoch') if robot else None
        if not before:
            return {**identity, 'connected':False, 'actions':[], 'prepare_state':'offline'}
        if before != resource.get('runtime_epoch'):
            raise ValueError('Runtime attachment changed; recover it before reading')
        if operation == 'capabilities':
            caps = robot.capabilities()
            result = dict(actions=[dict(name=a.name, description=a.description, parameters=a.parameters, kind=a.kind)
                                   for a in caps.operations], sensors=caps.sensors,
                          prepare_state=caps.prepare_state, manifest_revision=caps.manifest_revision)
        else:
            observed = robot.perceive()
            result = dict(observation_id='obs_' + uuid.uuid4().hex, delivered_at=time.time(), state=observed.state,
                images=[dict(name=i['name'], mimeType='image/png', data=base64.b64encode(i['png']).decode())
                        for i in observed.images if i.get('png')])
        current = self.resources.for_project(project_id)
        now_world = self.projects.worlds.get(resource['world_instance_id']) if world else None
        if (not current or any(current.get(k) != resource.get(k) for k in
                ('status','binding_generation','runtime_epoch','runtime_generation','world_instance_id'))
                or (world and any(now_world.get(k) != world.get(k) for k in
                    ('status', 'generation', 'topology_revision', 'epoch')))
                or (robot.health() or {}).get('epoch') != before):
            raise ValueError('Resource changed while reading; obtain a new observation')
        return {**result, **identity, 'robot_epoch':before, 'connected':True,
                'world_health': (now_world or {}).get('health', {'state':'not_observed'})}

    def _agent_control(self, record, pid, operation, args):
        with self._robot_control_locks.setdefault(pid, threading.RLock()):
            return self._agent_control_locked(record, pid, operation, args)

    def _end_run(self, record, pid, session):
        # Ending an owned standby Run must not require first acquiring motion.
        # Serialize with control transfer, and keep the exact Run identity so a
        # delayed request cannot end a replacement execution.
        with self._robot_control_locks.setdefault(pid, threading.RLock()):
            resource = self.resources.for_project(pid)
            active = self.projects.active_run(pid)
            control = self.control(pid)
            if (not resource or resource.get('client_id') != record['id']
                    or not active or active.get('client_id') != record['id']
                    or control.get('owner_id') not in (None, record['id'])):
                raise PermissionError('This Agent does not own the Run')
            if session.mode != 'sim':
                raise PermissionError('Real device lifecycle belongs to the operator')
            return self.projects.stop_run(pid, session.run_id)

    def _agent_control_locked(self, record, pid, operation, args):
        resource = self.resources.for_project(pid)
        if operation == 'control_release':
            self._owns(record, pid, allow_paused=True)
            return self.set_control(pid, accepting=False)
        if not resource or resource['mode'] != 'sim' or not resource.get('allow_control'):
            raise PermissionError('Operator has not granted autonomous control acquisition')
        world = self.projects.worlds.get(resource['world_instance_id'])
        if world['status'] != 'ready' or world.get('health', {}).get('state') == 'unknown':
            raise ValueError('World is unavailable or changing')
        if 'control' not in record['permissions']:
            raise PermissionError('This connection has observation permission only')
        current = self.control(pid)
        if current.get('owner_id') not in (None, record['id']):
            raise PermissionError('Another Agent owns this robot')
        session = self.projects.require_session(pid)
        robot, contract = self._supervision_contract(session)
        receipt = self._receipts.get((record['id'], pid))
        health = self._runtime_health(robot)
        if (not receipt or receipt['id'] != args['receipt_id'] or receipt['epoch'] != session.epoch
                or time.monotonic() - receipt['delivered_mono'] > contract['max_observation_age_s']
                or health.get('epoch') != receipt['body_epoch']):
            raise ValueError('Observe this robot before acquiring control')
        if health.get('ok') is False or type(health.get('held')) is not bool:
            return {'ok':False, **current, 'reason':'runtime_readiness_unknown',
                    'message':'Runtime hold state is unavailable; inspect it before requesting control.'}
        if current.get('accepting') and current.get('owner_id') == record['id']:
            if health['held']:
                return {'ok':False, **current, 'reason':'runtime_held', 'reacquisition_required':True,
                        'message':'Runtime is held. Release this control, obtain a fresh observation, '
                                  'and acquire new control before starting another task.'}
            return {'ok':True, **current}
        result = self.set_control(pid, accepting=True, owner_id=record['id'])
        if result.get('ok'):
            # Grant covers release from a confirmed supervised stop, never a fall
            # or an unconfirmed emergency hold.
            health = self._runtime_health(robot)
            if (health.get('ok') is False or health.get('epoch') != receipt['body_epoch']
                    or type(health.get('held')) is not bool):
                self._invalidate_control(pid)
                return {'ok':False, 'reason':'runtime_readiness_unknown',
                        'message':'Runtime changed or its hold state is unavailable; control remains disabled.'}
            try:
                if health['held']:
                    status = robot.status()
                    evidence = status.get('stop_evidence') if isinstance(status, dict) else None
                    if not isinstance(evidence, dict) or evidence.get('stop_confirmed') is not True:
                        self._invalidate_control(pid)
                        return {'ok':False, 'message':'Held robot has no current stable-stop evidence'}
                    if not self.hub.release(session.id).get('ok'):
                        self._invalidate_control(pid)
                        return {'ok':False, 'message':'Runtime refused release'}
                armed = self.hub.arm(session.id, True)
                if not armed.get('ok'):
                    self._invalidate_control(pid)
                    return {'ok':False, 'message':'Runtime did not authorize motion'}
            except Exception:
                self._invalidate_control(pid)
                return {'ok':False, 'reason':'runtime_readiness_unknown',
                        'message':'Runtime recovery could not be confirmed; control remains disabled.'}
        return {**result, 'next':'Observe and acknowledge a new frame before any action.'}

    def _dispatch(self, record, operation, args):
        # The complete receipt is persisted by the idempotency layer. A retry
        # keeps the original generation rather than relabelling old execution.
        run = self.projects.active_run(args['project_id'])
        if run is None and operation == 'run_start':
            self.reserve(args['project_id'], record['id'])
            run = self.projects.active_run(args['project_id'])
        elif run is None or run['id'] != _request_run.get():
            raise ValueError('Brain Run changed before dispatch')
        captured = {key: self.describe(run)[key] for key in IDENTITY_FIELDS}
        captured['control_generation'] = self.control(args['project_id']).get('control_generation', 0)
        if operation == 'run_start':
            expected = {key:run.get(key) for key in ('id','brain_instance_id','robot_instance_id',
                        'world_instance_id','binding_generation','world_generation')}
            result = self.projects.create_run(args['project_id'],operator=False,expected_run=expected)
        else:
            result = super()._dispatch(record, operation, args)
        if operation == 'run_start':
            current = next(r for r in self.projects.store.runs(args['project_id']) if r['id'] == result['run']['id'])
            transport = self._transports.get(current['brain_instance_id'] + ':' + record['id'])
            if transport is not None:
                transport['run_id'] = current['id']
            captured.update({key:self.describe(current)[key] for key in IDENTITY_FIELDS})
        if operation == 'observe':
            current = self.projects.active_run(args['project_id'])
            if current is None or any(self.describe(current)[key] != captured[key] for key in IDENTITY_FIELDS):
                raise ValueError('Brain or World changed while observing; request a new frame')
        if 'events' in result:
            result['events'] = [{**captured, **event} for event in result['events']]
        return {**result, **captured}
