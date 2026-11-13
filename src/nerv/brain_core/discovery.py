"""Authorized resource bootstrap; no physical truth or model decisions are exposed."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
import uuid

from .. import config
from ..platform.agent_gateway import _object, IDENT
from ..platform.projects import ProjectInput, _write, _id

DISCOVERY_OPERATIONS = {
    'platform_describe': ('Discover authorized Worlds and robot allocation grants.', _object()),
    'robot_allocate': ('Allocate one robot and persistent Brain in an authorized World.',
                       _object({'world_instance_id':IDENT, 'request_id':IDENT}, ['world_instance_id','request_id'])),
    'operation_get': ('Read the outcome of a resource request without replaying it.',
                      _object({'operation_id':IDENT}, ['operation_id'])),
    'brain_attach': ('Attach this connection to one authorized Brain, initially without motion authority.',
                    _object({'brain_instance_id':IDENT}, ['brain_instance_id'])),
    'robot_remove': ('Remove an owned simulation robot after ending its Run.',
                    _object({'robot_instance_id':IDENT, 'request_id':IDENT}, ['robot_instance_id','request_id'])),
}


class Discovery:
    def __init__(self, core):
        self.core = core
        self.lock = threading.RLock()
        self._world_locks = {}
        self._closed = False
        self._workers = ThreadPoolExecutor(max_workers=config.RESOURCE_OPERATION_WORKERS, thread_name_prefix='nerv-resource')
        # Recover identities and outcomes, never replay a possibly committed effect.
        for path in (core.root / 'resource-operations').glob('op_*.json'):
            value = json.loads(path.read_text())
            if value['status'] in {'pending', 'running'}:
                value.update(status='interrupted', finished_at=time.time(),
                             message='Host restarted; inspect allocated resource and World before recovery')
                _write(path, value)

    def close(self):
        with self.lock:
            self._closed = True
        # Quiesce owned work before the Host tears down its World processes.
        self._workers.shutdown(wait=True)

    def set_agent_topology(self, wid, *, allow_agent_changes, expected_topology_revision):
        """Operator permission change, serialized with the full allocation queue.

        Closing the gate leaves existing robots, leases and physics untouched.
        A request already executing finishes first; a stale operator view cannot
        silently freeze a different roster. Pending work rechecks the closed gate.
        """
        if (type(allow_agent_changes) is not bool or type(expected_topology_revision) is not int
                or expected_topology_revision < 1):
            raise ValueError('Provide a boolean permission and positive topology revision')
        with self.lock:
            world_lock = self._world_locks.setdefault(wid, threading.RLock())
        with world_lock, self.core.projects.worlds.lock:
            worlds = self.core.projects.worlds
            world = worlds.get(wid)
            if world['topology_revision'] != expected_topology_revision:
                raise ValueError('World topology changed; inspect the current roster first')
            world['topology_policy'] = {**world.get('topology_policy', {}),
                                       'allow_agent_changes': allow_agent_changes}
            worlds.save(world)
            return world

    def validate_grants(self, grants):
        if not isinstance(grants, list):
            raise ValueError('World grants must be a list')
        seen = set()
        for grant in grants:
            if set(grant) != {'world_instance_id','template_project_id','max_instances','spawn_slots','allow_control'}:
                raise ValueError('Specify World, template, quota, spawn slots and control permission')
            world = self.core.projects.worlds.get(grant['world_instance_id'])
            if world['world_instance_id'] in seen:
                raise ValueError('Only one grant per World is allowed')
            seen.add(world['world_instance_id'])
            template = self.core.projects.store.get(grant['template_project_id'])
            if (template['mode'] != 'sim' or template['world'] != world['world']
                    or template['robot'] != world['robot_profile'] or template.get('world_configuration')):
                raise ValueError('Allocation template does not match the World')
            if not world.get('topology_policy', {}).get('allow_agent_changes'):
                raise ValueError('World policy does not authorize Agent topology requests')
            if type(grant['allow_control']) is not bool or type(grant['max_instances']) is not int or grant['max_instances'] < 1:
                raise ValueError('Invalid control permission or quota')
            import math
            slots = grant['spawn_slots']
            if not isinstance(slots, list) or len(slots) < grant['max_instances']:
                raise ValueError('Provide a placement slot for each allowed instance')
            for slot in slots:
                if set(slot) != {'x','y','z','yaw'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in slot.values()):
                    raise ValueError('Placement coordinates must be finite')
        return grants

    def invoke(self, record, operation, args):
        core = self.core
        if operation == 'platform_describe':
            return {'resource_protocol':1, 'worlds':[
                {'world_instance_id':g['world_instance_id'], 'template_project_id':g['template_project_id'],
                 'max_instances':g['max_instances'], 'allow_control':g['allow_control'],
                 'status':core.projects.worlds.get(g['world_instance_id'])['status']}
                for g in record.get('world_grants', [])],
                'robots':[self.public(r) for r in core.resources.list() if r['client_id'] == record['id']],
                'topology_semantics':'Queued whole-world pause under operator policy; control never replays.'}
        if operation == 'brain_attach':
            _, run = core.resolve_binding(record['id'], args['brain_instance_id'])
            return core.describe(run)
        if operation == 'operation_get':
            value = json.loads((core.root / 'resource-operations' / (_id(args['operation_id']) + '.json')).read_text())
            if value['client_id'] != record['id']:
                raise PermissionError('Resource operation belongs to another principal')
            return value
        fingerprint = hashlib.sha256(json.dumps([operation,args], sort_keys=True, allow_nan=False).encode()).hexdigest()
        oid = 'op_' + hashlib.sha256((record['id'] + ':' + args['request_id']).encode()).hexdigest()
        path = core.root / 'resource-operations' / (oid + '.json')
        with self.lock:
            if path.exists():
                value = json.loads(path.read_text())
                if value['fingerprint'] != fingerprint:
                    raise ValueError('Request ID was already used with different arguments')
                return value
            if self._closed or getattr(core.hub, '_shutting_down', False):
                raise ValueError('Host is shutting down')
            value = dict(operation_id=oid, client_id=record['id'], fingerprint=fingerprint,
                         operation=operation, status='pending', created_at=time.time())
            _write(path, value)
            self._workers.submit(self._execute, record['id'], operation, dict(args), path, dict(value))
            return value

    def _execute(self, client_id, operation, args, path, value):
        core = self.core
        try:
            wid = (args['world_instance_id'] if operation == 'robot_allocate'
                   else core.resources.get(args['robot_instance_id'])['world_instance_id'])
            with self.lock:
                world_lock = self._world_locks.setdefault(wid, threading.RLock())
            # Quota/slot selection and physical changes share the same per-World queue.
            # Slow compilation never holds the request or cancellation gateway lock.
            with world_lock:
                with self.lock:
                    if self._closed or getattr(core.hub, '_shutting_down', False):
                        raise ValueError('Host stopped before this resource operation started')
                record = json.loads(core._path(client_id).read_text())
                if record.get('revoked'):
                    raise PermissionError('Allocation principal has been revoked')
                value.update(status='running', started_at=time.time())
                _write(path, value)
                if operation == 'robot_allocate':
                    def checkpoint(resource):
                        value['resource'] = self.public(resource)
                        _write(path, value)
                    result = self.allocate(record, wid, checkpoint=checkpoint, operation_id=value['operation_id'])
                else:
                    resource = core.resources.get(args['robot_instance_id'])
                    if resource['client_id'] != record['id'] or resource['mode'] != 'sim':
                        raise PermissionError('Robot removal is outside this grant')
                    self.grant(record, wid)
                    value['resource'] = self.public(resource)
                    _write(path, value)
                    core.projects.worlds.remove_robot(resource)
                    result = core.resources.get(resource['robot_instance_id'])
                value.update(status='complete', resource=self.public(result))
        except Exception as error:
            # Includes IO/transport loss after a physical commit. Retain the operation
            # and any allocated identity, and never automatically retry side effects.
            value.update(status='failed', error_type=type(error).__name__, message=str(error))
            if value.get('resource'):
                try:
                    value['resource'] = self.public(core.resources.get(value['resource']['robot_instance_id']))
                except (OSError, ValueError):
                    pass  # Keep the last durable identity even if storage also failed.
                value['recovery_required'] = True
        value['finished_at'] = time.time()
        _write(path, value)

    def grant(self, record, wid):
        grant = next((g for g in record.get('world_grants', []) if g['world_instance_id'] == wid), None)
        if grant is None:
            raise PermissionError('World is outside this allocation grant')
        world = self.core.projects.worlds.get(wid)
        if not world.get('topology_policy', {}).get('allow_agent_changes'):
            raise PermissionError('World topology requests are disabled')
        return grant

    def allocate(self, record, wid, *, checkpoint, operation_id):
        core = self.core
        grant = self.grant(record, wid)
        existing = [r for r in core.resources.list() if r['client_id'] == record['id']
                    and r['world_instance_id'] == wid and r['status'] != 'removed']
        if len(existing) >= grant['max_instances']:
            raise ValueError('Allocation quota exhausted')
        used = {r.get('spawn_slot') for r in existing}
        index = next(i for i in range(len(grant['spawn_slots'])) if i not in used)
        template = core.projects.store.get(grant['template_project_id'])
        project = core.projects.create({k:v for k,v in template.items() if k in ProjectInput.model_fields}
                                       | {'name':template['name'] + ' ' + uuid.uuid4().hex[:8]})
        resource = core.resources.reserve(project, record['id'])
        resource.update(world_instance_id=wid, shared_world=True, spawn_slot=index,
                        allow_control=grant['allow_control'], allocation_operation_id=operation_id)
        core.resources.save(resource)
        checkpoint(resource)
        # Scope is extended only to this newly created Project, never the template.
        with core._lock:
            current = json.loads(core._path(record['id']).read_text())
            current['projects'].append(project['id'])
            _write(core._path(record['id']), current)
        core.projects.worlds.add_robot(resource, grant['spawn_slots'][index])
        return core.resources.get(resource['robot_instance_id'])

    @staticmethod
    def public(resource):
        return {k:resource.get(k) for k in ('robot_instance_id','brain_instance_id','world_instance_id',
                'project_id','status','mode','binding_generation')}
