"""Operator-owned frozen World composition, separate from individual Run lifetimes."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import uuid

from ..platform.projects import _write, _id


class WorldInstances:
    def __init__(self, projects):
        self.projects, self.hub = projects, projects.hub
        self.root = projects.store.root.parent / 'world-instances'
        self.lock = threading.RLock()
        # No process resurrection or movement after Host recovery.
        for path in self.root.glob('world_*.json'):
            value = json.loads(path.read_text())
            if value['status'] in {'preparing', 'ready'}:
                value.update(status='interrupted', reason='Host restarted; create a new World')
                _write(path, value)

    def get(self, wid):
        return json.loads((self.root / (_id(wid) + '.json')).read_text())

    def save(self, value):
        _write(self.root / (value['world_instance_id'] + '.json'), value)

    def for_run(self, run):
        if run and run.get('shared_world'):
            return self.get(run['world_instance_id'])
        return None

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
                               'run_id': run['id'], 'spawn': dict(pose)})
            if len({p['id'] for p in projects}) != len(projects):
                raise ValueError('A Project controls exactly one robot')
            if len({(p['world'], p['robot'], json.dumps(p['world_configuration'], sort_keys=True)) for p in projects}) != 1:
                raise ValueError('0.9.1 shared composition requires a common scene, robot profile and configuration')
            if projects[0]['world_configuration']:
                raise ValueError('Shared composition currently requires the registered scene revision')
            wid = 'world_' + uuid.uuid4().hex
            world = {'world_instance_id': wid, 'status': 'configured', 'generation': 1,
                     'world': projects[0]['world'], 'robot_profile': projects[0]['robot'],
                     'roster': roster, 'created_at': time.time()}
            self.save(world)
            for run in runs:
                run.update(world_instance_id=wid, shared_world=True)
                self.projects.store.save_run(run['project_id'], run)
            return world

    def prepare(self, world):
        with self.lock:
            world = self.get(world['world_instance_id'])
            if world['status'] == 'ready':
                return world
            if world['status'] != 'configured':
                raise ValueError('World is not startable; inspect its recorded failure')
            hub = self.hub
            spec = hub.registry.world(world['world'])
            body = hub.registry.body(world['robot_profile'])
            request = {'world_instance_id': world['world_instance_id'],
                       'arena': str(Path(spec.assets_root) / spec.supports[body.name]),
                       'physics': dict(spec.physics),
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
                    runtime = hub.launcher.ensure_runtime(profile, 'sim', shared_spec, wh, defer_prepare=True)
                    runtimes.append(runtime)
                # Preparation needs every bus to spawn before the shared clock advances.
                # These are IO workers, not AI Agents or model calls.
                with ThreadPoolExecutor(max_workers=len(runtimes)) as pool:
                    list(pool.map(hub.launcher._prepare_runtime, runtimes))
                world.update(status='ready', epoch=handle.meta['epoch'], receipt=handle.meta.get('configuration', {}))
                self.save(world)
                return world
            except Exception as error:
                world.update(status='failed', reason=str(error))
                self.save(world)
                # Preserve uncertainty and let operator stop the entire World.
                raise

    def attach(self, run):
        world = self.prepare(self.for_run(run))
        p = run['snapshot']
        s, frozen = self.hub.store.new('operator', run['robot_instance_id'], world['world_instance_id'],
            sensors=p['sensors'], tools=[], epoch=world['epoch'], project_id=p['id'], run_id=run['id'],
            project_revision=run['project_revision'], mode='sim')
        s.robot_profile = p['robot']
        s.robot_instance_id = run['robot_instance_id']
        s.world_instance_id = world['world_instance_id']
        s.brain_instance_id = run['brain_instance_id']
        s.binding = f'{s.body}:sim:{s.world}'
        self.hub.store.save(s)
        return s.summary()

    def stop(self, wid):
        with self.lock:
            world = self.get(wid)
            stops = []
            for slot in world['roster']:
                run = self.projects.active_run(slot['project_id'])
                if run and run.get('session_id'):
                    result = self.hub.tasks.cancel(run['session_id'], reason='Operator stopped shared World')
                    stops.append(result)
                    self.hub.store.set_armed(run['session_id'], False)
                    self.hub.store.set_status(run['session_id'], 'reconnect_required')
                if run:
                    run.update(status='interrupted', reason='Shared World stopped by operator', ended_at=time.time())
                    self.projects.store.save_run(run['project_id'], run)
                rid = slot['robot_instance_id']
                self.hub.launcher.stop('body:' + rid)
                self.hub._bodies.pop(rid, None)
                self.hub.launcher.nodes.pop(f'world:{wid}/{rid}', None)
                self.hub._worlds.pop(f'{wid}/{rid}', None)
            self.hub.launcher.stop('shared_world:' + wid)
            world.update(status='stopped', generation=world['generation']+1)
            self.save(world)
            return {'ok': all(s.get('ok') for s in stops), 'world': world, 'stops': stops}
