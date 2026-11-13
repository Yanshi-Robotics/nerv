"""Operator-owned World lifecycle and serialized membership transactions."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from ..platform.projects import _write, _id


def source_revision(spec):
    """Pin scene inputs before launch, including meshes/textures and generators."""
    root = Path(spec.assets_root).resolve()
    if not root.is_dir():
        raise ValueError('Registered scene assets are unavailable')
    digest = hashlib.sha256()
    count = 0
    extensions = {'.xml', '.obj', '.stl', '.png', '.jpg', '.jpeg', '.webp', '.yaml', '.yml', '.json', '.py'}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
            continue
        if path.suffix.lower() not in extensions or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError('Scene input escapes its registered asset root')
        digest.update(relative.as_posix().encode() + b'\0')
        with path.open('rb') as stream:
            file_hash = hashlib.file_digest(stream, 'sha256').digest()
        digest.update(file_hash)
        count += 1
    if not count:
        raise ValueError('Registered scene has no revisionable inputs')
    return {'sha256':digest.hexdigest(), 'files':count}


class WorldInstances:
    def __init__(self, projects):
        self.projects, self.hub = projects, projects.hub
        self.root = projects.store.root.parent / 'world-instances'
        self.lock = threading.RLock()
        self._last_health = 0.0
        # No process resurrection or movement after Host recovery.
        for path in self.root.glob('world_*.json'):
            value = json.loads(path.read_text())
            if value['status'] in {'preparing', 'ready', 'changing'}:
                value.update(status='interrupted', reason='Host restarted; operator restart required')
                _write(path, value)

    def get(self, wid):
        return json.loads((self.root / (_id(wid) + '.json')).read_text())

    def save(self, value):
        _write(self.root / (value['world_instance_id'] + '.json'), value)

    def for_run(self, run):
        if run and run.get('shared_world'):
            return self.get(run['world_instance_id'])
        return None

    def create(self, scene, robot_profile, *, topology_policy=None):
        """Operator-created empty World. A profile selects the scene's template only."""
        spec = self.hub.registry.world(scene)
        body = self.hub.registry.body(robot_profile)
        if spec.kind != 'sim' or body.name not in spec.supports:
            raise ValueError('Select a compatible simulation scene and robot template')
        policy = dict(topology_policy or {})
        if set(policy) - {'allow_agent_changes', 'max_robots'}:
            raise ValueError('Unknown World topology policy')
        if type(policy.get('allow_agent_changes', False)) is not bool:
            raise ValueError('Specify whether Agent topology requests are allowed')
        if type(policy.get('max_robots', 2)) is not int or policy.get('max_robots', 2) < 1:
            raise ValueError('World capacity must be a positive integer')
        world = dict(world_instance_id='world_' + uuid.uuid4().hex, world=scene,
                     robot_profile=robot_profile, status='configured', generation=1,
                     topology_revision=1, roster=[], created_at=time.time(),
                     source_revision=source_revision(spec), topology_policy=policy)
        self.save(world)
        return world

    def list(self):
        return [json.loads(path.read_text()) for path in sorted(self.root.glob('world_*.json'))]

    def add_robot(self, resource, spawn):
        """Allocate the physical entity before any Run is allowed to start."""
        with self.lock:
            world = self.get(resource['world_instance_id'])
            if len(world['roster']) >= world.get('topology_policy', {}).get('max_robots', 2):
                raise ValueError('World capacity exhausted')
            if any(r['robot_instance_id'] == resource['robot_instance_id'] for r in world['roster']):
                return world
            if world['status'] != 'ready':
                raise ValueError('Start the existing World before allocating a robot')
            slot = dict(robot_instance_id=resource['robot_instance_id'], project_id=resource['project_id'],
                        run_id=None, spawn=spawn)
            body = self.hub.registry.body(world['robot_profile'])
            result = self._change_topology(world, add=slot)
            world.update(roster=world['roster'] + [slot], topology_revision=result['topology_revision'],
                         generation=world['generation'] + 1, status='ready')
            self.save(world)
            self._sync_generation(world)
            # Keep the committed entity discoverable even if Runtime preparation fails.
            self.projects.resources.save(dict(resource, shared_world=True, status='preparing'))
            try:
                handle = self.hub.launcher.nodes[f"world:{world['world_instance_id']}/{resource['robot_instance_id']}"]
                spec = self.hub.registry.world(world['world']).model_copy(update={'name':world['world_instance_id']})
                runtime = self.hub.launcher.ensure_runtime(body.model_copy(update={'name':resource['robot_instance_id']}),
                    'sim', spec, handle, scene_profile=world['world'])
                self.projects.resources.save(dict(resource, shared_world=True, status='ready', runtime_epoch=runtime.meta.get('epoch')))
            except Exception as error:
                self.projects.resources.save(dict(resource, shared_world=True, status='failed', reason=str(error)))
                world.update(status='recovery_required', reason='Entity committed but Runtime preparation failed; operator stop and restart required')
                self.save(world)
                raise
            return world

    def _change_topology(self, world, **change):
        world['status'] = 'changing'
        self.save(world)
        try:
            self._quiesce(world)
            return self.hub.launcher.change_shared_world(world, **change)
        except ValueError:
            # Backend validation/rollback confirmed no structural commit.
            world['status'] = 'ready'
            self.save(world)
            raise
        except Exception:
            world.update(status='recovery_required', reason='Topology outcome unknown; inspect World receipt')
            self.save(world)
            raise

    def _sync_generation(self, world):
        for slot in world['roster']:
            run = self.projects.active_run(slot['project_id'])
            if run:
                run['world_generation'] = world['generation']
                self.projects.store.save_run(run['project_id'], run)
                if run.get('session_id'):
                    session = self.hub.store.get(run['session_id'])
                    session.world_generation = world['generation']
                    self.hub.store.save(session)

    def _quiesce(self, world):
        for slot in world['roster']:
            pid = slot['project_id']
            core = getattr(self.projects, 'brain_core', None)
            if core is not None:
                with core._lock:
                    core._invalidate_control(pid)
            run = self.projects.active_run(pid)
            if run and run.get('session_id'):
                result = self.hub.tasks.cancel(run['session_id'], reason='World topology transaction')
                if not result.get('ok'):
                    raise ValueError('Execution stop unconfirmed; World topology unchanged')
            client = self.hub.body_client(slot['robot_instance_id'])
            result = client.hold('world_topology') if client else {}
            if not result.get('stop_confirmed'):
                raise ValueError('Physical stop unconfirmed; World topology unchanged')

    def remove_robot(self, resource):
        with self.lock:
            world = self.get(resource['world_instance_id'])
            if self.projects.active_run(resource['project_id']):
                raise ValueError('End the robot Run before removing its entity')
            result = self._change_topology(world, remove=resource['robot_instance_id'])
            world.update(roster=[r for r in world['roster'] if r['robot_instance_id'] != resource['robot_instance_id']],
                         topology_revision=result['topology_revision'], generation=world['generation'] + 1, status='ready')
            self.save(world)
            self._sync_generation(world)
            self.hub.launcher.stop('body:' + resource['robot_instance_id'])
            self.hub._bodies.pop(resource['robot_instance_id'], None)
            self.projects.resources.save(dict(resource, status='removed', removed_at=time.time()))
            return world

    def configure(self, slots):
        """Freeze already allocated Runs; only explicit operator API calls reach here."""
        with self.lock, self.projects._lock:
            if not isinstance(slots, list) or len(slots) < 2:
                raise ValueError('Shared World needs at least two allocated Runs')
            projects, runs, roster = [], [], []
            for slot in slots:
                if set(slot) != {'project_id', 'spawn'}:
                    raise ValueError('Each slot needs project_id and explicit spawn')
                project = self.projects.store.get(slot['project_id'])
                run = self.projects.active_run(project['id'])
                if not run or run['status'] != 'reserved' or run.get('shared_world'):
                    raise ValueError('Allocate an unstarted Brain before composing the World')
                if project['mode'] != 'sim' or project['revision'] != run['project_revision']:
                    raise ValueError('Simulation Project revision changed; allocate a new Run')
                pose = slot['spawn']
                import math
                if set(pose) != {'x', 'y', 'z', 'yaw'} or any(type(v) not in (int, float) or not math.isfinite(v) for v in pose.values()):
                    raise ValueError('Specify finite x, y, z and yaw for each spawn')
                projects.append(project)
                runs.append(run)
                roster.append({'robot_instance_id': run['robot_instance_id'], 'project_id': project['id'],
                               'run_id': run['id'], 'project_name':project['name'], 'spawn': dict(pose)})
            if len({p['id'] for p in projects}) != len(projects):
                raise ValueError('A Project controls exactly one robot')
            if len({(p['world'], p['robot'], json.dumps(p['world_configuration'], sort_keys=True)) for p in projects}) != 1:
                raise ValueError('0.9.1 shared composition requires a common scene, robot profile and configuration')
            if projects[0]['world_configuration']:
                raise ValueError('Shared composition currently requires the registered scene revision')
            wid = 'world_' + uuid.uuid4().hex
            world = {'world_instance_id': wid, 'status': 'configured', 'generation': 1,
                     'world': projects[0]['world'], 'robot_profile': projects[0]['robot'],
                     'roster': roster, 'created_at': time.time(),
                     'source_revision':source_revision(self.hub.registry.world(projects[0]['world']))}
            self.save(world)
            for run in runs:
                if hasattr(self.projects, 'resources'):
                    self.projects.resources.bind_world(run['project_id'], wid)
                run.update(world_instance_id=wid, world_generation=world["generation"], shared_world=True)
                self.projects.store.save_run(run['project_id'], run)
            return world

    def prepare(self, world, *, restart=False):
        with self.lock:
            world = self.get(world['world_instance_id'])
            if world['status'] == 'ready':
                return world
            if world['status'] in {'interrupted', 'stopped'} and restart:
                world.update(status='configured', generation=world['generation'] + 1,
                             topology_revision=world.get('topology_revision', 1) + 1)
                self.save(world)
            if world['status'] != 'configured':
                raise ValueError('World is not startable; operator recovery is required')
            hub = self.hub
            spec = hub.registry.world(world['world'])
            body = hub.registry.body(world['robot_profile'])
            if source_revision(spec) != world['source_revision']:
                raise ValueError('Scene inputs changed after World allocation; compose a new World')
            request = {'world_instance_id': world['world_instance_id'],
                       'topology_revision': world.get('topology_revision', 1),
                       'arena': str(Path(spec.assets_root) / spec.supports[body.name]),
                       'physics': dict(spec.physics), 'source_revision':world['source_revision'],
                       'body_profile': {'actuators': body.actuators, 'dir': body.dir},
                       'roster': [dict(r) for r in world['roster']]}
            world['status'] = 'preparing'
            self.save(world)
            try:
                handle = hub.launcher.ensure_shared_world(spec, body, request)
                shared_spec = spec.model_copy(update={'name': world['world_instance_id']})
                runtimes = []
                for slot in world['roster']:
                    rid = slot['robot_instance_id']
                    profile = body.model_copy(update={'name': rid})
                    wh = hub.launcher.nodes[f"world:{world['world_instance_id']}/{rid}"]
                    runtime = hub.launcher.ensure_runtime(profile, 'sim', shared_spec, wh, defer_prepare=True, scene_profile=world['world'])
                    runtimes.append(runtime)
                # Preparation needs every bus to spawn before the shared clock advances.
                # These are IO workers, not AI Agents or model calls.
                if runtimes:
                    with ThreadPoolExecutor(max_workers=len(runtimes)) as pool:
                        list(pool.map(hub.launcher._prepare_runtime, runtimes))
                world.update(status='ready', epoch=handle.meta['epoch'], receipt=handle.meta.get('configuration', {}))
                self.save(world)
                self._sync_generation(world)
                resources = getattr(self.projects, 'resources', None)
                if resources is not None:
                    for slot, runtime in zip(world['roster'], runtimes, strict=True):
                        resource = resources.get(slot['robot_instance_id'])
                        resources.save(dict(resource, status='ready', runtime_epoch=runtime.meta.get('epoch')))
                return world
            except Exception as error:
                world.update(status='failed', reason=str(error))
                self.save(world)
                # Preserve uncertainty and let operator stop the entire World.
                raise

    def attach(self, run):
        # Run creation attaches metadata only. Starting/recovering physics and
        # attaching a Runtime belong to explicit resource lifecycle operations.
        world = self.for_run(run)
        if not world or world['status'] != 'ready':
            raise ValueError('Start the existing World before starting its Run')
        resource = self.projects.resources.for_project(run['project_id'])
        if (not resource or resource['status'] != 'ready'
                or resource['robot_instance_id'] != run['robot_instance_id']
                or resource['world_instance_id'] != world['world_instance_id']
                or not any(slot['robot_instance_id'] == run['robot_instance_id']
                           and slot['project_id'] == run['project_id'] for slot in world['roster'])):
            raise ValueError('Attach the allocated robot Runtime before starting its Run')
        p = run['snapshot']
        if p['sensors']:
            client = self.hub.world_client(world['world_instance_id'], run['robot_instance_id'])
            offered = set(client.sensors()) if client else set()
            bad = [sensor for sensor in p['sensors'] if sensor not in offered]
            if bad:
                raise ValueError(f"World does not publish sensor streams {bad}; it offers {sorted(offered)}")
        s, frozen = self.hub.store.new('operator', run['robot_instance_id'], world['world_instance_id'],
            sensors=p['sensors'], tools=[], epoch=world['epoch'], project_id=p['id'], run_id=run['id'],
            project_revision=run['project_revision'], mode='sim')
        s.robot_profile = p['robot']
        s.robot_instance_id = run['robot_instance_id']
        s.world_instance_id = world['world_instance_id']
        s.brain_instance_id = run['brain_instance_id']
        s.binding = f'{s.body}:sim:{s.world}'
        self.hub.store.save(s)
        for slot in world['roster']:
            if slot['robot_instance_id'] == run['robot_instance_id']:
                slot['run_id'] = run['id']
        self.save(world)
        return s.summary()

    def check_health(self):
        """Observe child-process loss; never restart or reclaim a lost instance."""
        from .. import config
        if time.monotonic() - self._last_health < config.NODE_PROBE_TIMEOUT:
            return []
        self._last_health = time.monotonic()
        failures = []
        invalidated = set()
        for path in self.root.glob('world_*.json'):
            world = json.loads(path.read_text())
            if world['status'] != 'ready':
                continue
            owner = self.hub.launcher.nodes.get('shared_world:' + world['world_instance_id'])
            # Process liveness and the physics thread health are separate facts.
            import httpx
            try:
                healthy = owner and owner.alive()
                health = httpx.get(owner.url + '/health', timeout=config.NODE_PROBE_TIMEOUT).json() if healthy else {}
                healthy = healthy and health.get('ok') and health.get('epoch') == world.get('epoch')
            except (httpx.HTTPError, ValueError):
                healthy = False
            with self.lock:
                current = self.get(world['world_instance_id'])
                if any(current.get(key) != world.get(key) for key in
                       ('status', 'generation', 'topology_revision', 'epoch')):
                    continue  # The probe predates a topology/restart transaction.
                for slot in world['roster']:
                    runtime = self.hub.launcher.nodes.get('body:' + slot['robot_instance_id'])
                    if healthy and runtime and runtime.alive():
                        continue
                    reason = 'Shared World unavailable' if not healthy else 'Robot Runtime unavailable'
                    resources = getattr(self.projects, 'resources', None)
                    if resources is not None:
                        resource = resources.get(slot['robot_instance_id'])
                        if resource['status'] not in {'removed', 'failed'}:
                            resources.save(dict(resource, status='failed', reason=reason))
                            invalidated.add(slot['project_id'])
                    run = self.projects.active_run(slot['project_id'])
                    if not run or run['id'] != slot['run_id'] or run['status'] != 'running':
                        continue
                    invalidated.add(slot['project_id'])
                    run.update(status='failed', reason=reason, ended_at=time.time())
                    self.projects.store.save_run(run['project_id'], run)
                    self.hub.store.set_armed(run['session_id'], False)
                    self.hub.store.set_status(run['session_id'], 'reconnect_required')
                    failures.append((run, reason))
                if not healthy:
                    world.update(status='failed', reason='Shared World unavailable', generation=world['generation']+1)
                    self.save(world)
        def stop_failed(item):
            run, reason = item
            return self.hub.tasks.cancel(run['session_id'], reason=reason)
        if failures:
            with ThreadPoolExecutor(max_workers=len(failures)) as pool:
                list(pool.map(stop_failed, failures))
        return sorted(invalidated)

    def stop(self, wid):
        with self.lock:
            world = self.get(wid)
            stops = []
            for slot in world['roster']:
                run = self.projects.active_run(slot['project_id'])
                if run and run['id'] != slot['run_id']:
                    run = None
                if run and run.get('session_id'):
                    result = self.hub.tasks.cancel(run['session_id'], reason='Operator stopped shared World')
                    stops.append(result)
                    self.hub.store.set_armed(run['session_id'], False)
                    self.hub.store.set_status(run['session_id'], 'reconnect_required')
                if run:
                    run.update(status='interrupted', reason='Shared World stopped by operator', ended_at=time.time())
                    self.projects.store.save_run(run['project_id'], run)
                rid = slot['robot_instance_id']
                resources = getattr(self.projects, 'resources', None)
                if resources is not None:
                    resources.save(dict(resources.get(rid), status='offline'))
                core = getattr(self.projects, 'brain_core', None)
                if core is not None:
                    with core._lock:
                        core._invalidate_control(slot['project_id'])
                self.hub.launcher.stop('body:' + rid)
                self.hub._bodies.pop(rid, None)
                self.hub.launcher.nodes.pop(f'world:{wid}/{rid}', None)
                self.hub._worlds.pop(f'{wid}/{rid}', None)
            self.hub.launcher.stop('shared_world:' + wid)
            world.update(status='stopped', generation=world['generation']+1)
            self.save(world)
            return {'ok': all(s.get('ok') for s in stops), 'world': world, 'stops': stops}
