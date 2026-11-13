"""Operator-owned device attachment, independent of Run and simulation lifetime."""
import threading
import time
import uuid


class RuntimeAttachments:
    def __init__(self, core):
        self.core, self.projects, self.hub = core, core.projects, core.hub
        self._lock = threading.RLock()

    def connect(self, project_id, *, operator=False):
        if not operator:
            raise PermissionError('A person must connect registered devices')
        with self._lock:
            project = self.projects.store.get(project_id)
            if project['mode'] != 'real':
                raise ValueError('Simulation attachments belong to their World')
            profile = self.hub.registry.body(project['robot'])
            binding = profile.buses.get('real')
            if binding is None or not binding.enabled:
                raise ValueError('Device profile has no enabled real binding')
            resource = self.core.resources.for_project(project_id)
            if resource and resource['status'] == 'ready':
                client = self.hub.body_client(resource['robot_instance_id'])
                if client and (client.health() or {}).get('epoch') == resource.get('runtime_epoch'):
                    return resource
                raise ValueError('Device epoch changed; disconnect before reconnecting')
            if resource and resource.get('runtime_attachment', {}).get('status') == 'close_unconfirmed':
                raise ValueError('Confirm device disconnect before another preparation')
            run = self.projects.active_run(project_id)
            if run and run['status'] != 'reserved':
                raise ValueError('End the previous Run before reconnecting')
            # A registered device profile represents one connection endpoint. Two
            # Projects cannot prepare that same endpoint under different node IDs.
            for other in self.core.resources.list():
                attachment = other.get('runtime_attachment', {})
                if (other['project_id'] != project_id and attachment.get('profile_id') == profile.name
                        and attachment.get('status') in {'preparing', 'ready', 'close_unconfirmed'}):
                    raise ValueError('Registered device is already attached to another Robot')
            resource = resource or self.core.resources.reserve(project, None)
            attachment = dict(id='attachment_' + uuid.uuid4().hex,
                generation=resource.get('runtime_generation', 0) + 1, profile_id=profile.name,
                status='preparing', created_at=time.time())
            resource.update(world_instance_id=None, shared_world=False, status='preparing',
                            runtime_attachment=attachment, runtime_generation=attachment['generation'])
            self.core.resources.save(resource)
            rid = resource['robot_instance_id']
            try:
                self.hub.launcher.ensure_runtime(profile.model_copy(update={'name':rid}), 'real')
                client = self.hub.body_client(rid)
                if client is None or not client.post('/prepare', {}).get('ok'):
                    raise RuntimeError('Device preparation was not confirmed')
                epoch = (client.health() or {}).get('epoch')
                if not epoch:
                    raise RuntimeError('Device attachment has no Runtime epoch')
                attachment.update(status='ready', epoch=epoch)
                resource.update(status='ready', runtime_epoch=epoch)
            except Exception:
                # A failed preparation may have connected a device. Preserve the
                # uncertain attachment and require explicit close/recovery.
                attachment['status'] = 'close_unconfirmed'
                resource['status'] = 'failed'
                self.core.resources.save(resource)
                raise
            return self.core.resources.save(resource)

    def disconnect(self, robot_id, *, operator=False):
        if not operator:
            raise PermissionError('A person must disconnect registered devices')
        with self._lock:
            resource = self.core.resources.get(robot_id)
            if resource['mode'] != 'real':
                raise ValueError('Simulation removal belongs to its World')
            if self.projects.active_run(resource['project_id']):
                raise ValueError('End the Run before disconnecting its device')
            with self.core._lock:
                self.core._invalidate_control(resource['project_id'])
            client = self.hub.body_client(robot_id)
            result = client.post('/device/close', {}) if client else {'ok':False}
            attachment = resource.get('runtime_attachment', {})
            if not result.get('ok'):
                attachment['status'] = 'close_unconfirmed'
                resource.update(status='failed', runtime_attachment=attachment)
                self.core.resources.save(resource)
                raise ValueError('Device disconnect is unconfirmed')
            self.hub.launcher.stop('body:' + robot_id)
            self.hub._bodies.pop(robot_id, None)
            attachment.update(status='offline', ended_at=time.time())
            resource.update(status='offline', runtime_attachment=attachment)
            return self.core.resources.save(resource)

    def attach_run(self, run):
        resource = self.core.resources.for_project(run['project_id'])
        if not resource or resource['status'] != 'ready':
            raise ValueError('Connect the registered device before starting its Run')
        rid = resource['robot_instance_id']
        client = self.hub.body_client(rid)
        if client is None or (client.health() or {}).get('epoch') != resource.get('runtime_epoch'):
            raise ValueError('Device attachment changed; reconnect explicitly')
        project = run['snapshot']
        run.update({key:resource.get(key) for key in ('robot_instance_id','brain_instance_id',
                   'world_instance_id','binding_generation','client_id','shared_world')})
        run.update(runtime_attachment_id=resource['runtime_attachment']['id'],
                   runtime_generation=resource['runtime_generation'], world_generation=0)
        session, _ = self.hub.store.new('operator', rid, None, sensors=project['sensors'], tools=[],
            epoch=resource['runtime_epoch'], project_id=project['id'], run_id=run['id'],
            project_revision=run['project_revision'], mode='real')
        return session.summary()
