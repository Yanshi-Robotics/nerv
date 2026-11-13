"""Process entry point for an operator-configured shared physical World."""
import argparse
import json
import os

from fastapi import FastAPI, Request, HTTPException
import secrets
import threading
import time

from .composition import SharedWorld
from .mujoco_node import build_app, make_bus_handler
from .bus_server import BusServer


def prepare_rendering(owner):
    """Pay GL/context and first-frame costs before publishing World readiness.

    Called before startup or while the Host's topology transaction has already
    revoked motion. These diagnostic pixels are discarded, never observations
    or acknowledgements. A headless World may retain physics with failed sensors.
    """
    states = {}
    for rid, view in owner.views.items():
        started = time.monotonic()
        try:
            cameras = view.model_cameras()
            def initialize(renderer):
                if cameras:
                    with view._lock:
                        renderer.update_scene(view.data, camera=view._camera_for(cameras[0]))
                    renderer.render()
            view.render.run(initialize)
            states[rid] = {'state':'ready', 'elapsed_s':time.monotonic() - started}
        except Exception as error:
            states[rid] = {'state':'failed', 'elapsed_s':time.monotonic() - started,
                           'error_type':type(error).__name__, 'message':str(error)}
    owner.receipt['rendering_readiness'] = states
    return states

def robot_app(view):
    origins = [value.strip() for value in os.environ.get("NERV_CORS_ORIGINS", "http://localhost:8100").split(",") if value.strip()]
    return build_app(view, origins)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--configuration', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    with open(args.configuration) as file:
        config = json.load(file)
    from types import SimpleNamespace
    from .mujoco_node import _body_defaults_from_registry
    defaults = _body_defaults_from_registry(SimpleNamespace(**config['body_profile']))
    for slot in config['roster']:
        slot['body_defaults'] = defaults
    owner = SharedWorld(config['arena'], config['roster'],
                        world_instance_id=config['world_instance_id'], physics=config['physics'])
    owner.topology_revision = config.get('topology_revision', 1)
    owner.receipt['topology_revision'] = owner.topology_revision
    app = FastAPI(title='NERV shared World')
    buses = {}
    topology_lock = threading.Lock()

    @app.get('/health')
    def health():
        import os
        return {'ok': owner.running, 'node': 'world', 'epoch': owner.epoch,
                'world': owner.world_instance_id, 'error': owner.error,
                'launch_token': os.environ.get('NERV_LAUNCH_TOKEN', ''),
                'configuration': owner.receipt}

    @app.get('/status')
    def status():
        return {'world_instance_id': owner.world_instance_id, 'epoch': owner.epoch,
                'robots': {key: view.status() for key, view in owner.views.items()}}

    @app.get('/operator/geometry')
    def geometry():
        return {'world_instance_id':owner.world_instance_id, 'epoch':owner.epoch,
                'robots':{rid:view.operator_geometry() for rid,view in owner.views.items()},
                'collision_geometry':owner.geometry_boxes()}

    def authorize(request):
        expected = os.environ.get('NERV_LAUNCH_TOKEN', '')
        if not expected or not secrets.compare_digest(expected, request.headers.get('x-nerv-world-token', '')):
            raise HTTPException(403, 'Host World authorization required')

    @app.post('/operator/evidence')
    def evidence_start(request: Request, payload: dict):
        authorize(request)
        from .evidence import PhysicsEvidence
        required = {'sample_hz', 'duration_s', 'max_samples'}
        if not required <= set(payload) or set(payload) - required - {'obstacle_margin_m'}:
            raise HTTPException(400, 'Specify recording rate, duration and capacity')
        with owner.lock:
            if owner.evidence is not None:
                raise HTTPException(409, 'This World already has an evidence record; retrieve it before another trial')
            owner.evidence = PhysicsEvidence(**payload)
        return {'ok':True, 'world_instance_id':owner.world_instance_id, 'epoch':owner.epoch}

    @app.get('/operator/evidence')
    def evidence_read(request: Request, after: int = 0):
        authorize(request)
        with owner.lock:
            if owner.evidence is None:
                raise HTTPException(404, 'No recording')
            return owner.evidence.read(after)

    @app.post('/operator/evidence/stop')
    def evidence_stop(request: Request, after: int = 0):
        authorize(request)
        with owner.lock:
            if owner.evidence is None:
                raise HTTPException(404, 'No recording')
            owner.evidence.finished = True
            return owner.evidence.read(after)

    @app.post('/operator/topology')
    def topology(request: Request, payload: dict):
        expected = os.environ.get('NERV_LAUNCH_TOKEN', '')
        if not expected or not secrets.compare_digest(expected, request.headers.get('x-nerv-world-token', '')):
            raise HTTPException(403, 'Host topology authorization required')
        if set(payload) not in ({'expected_revision', 'add'}, {'expected_revision', 'remove'}):
            raise HTTPException(400, 'Provide one topology change and expected_revision')
        with topology_lock:
            new_bus = None
            try:
                roster = list(owner.roster)
                if 'add' in payload:
                    slot = dict(payload['add'], body_defaults=defaults)
                    rid = slot['robot_instance_id']
                    if rid in owner.views:
                        raise ValueError('Robot identity already exists')
                    new_bus = BusServer(slot['bus_url'], make_bus_handler(owner.endpoint(rid)))
                    new_bus.start()
                    roster.append(slot)
                else:
                    rid = payload['remove']
                    view = owner.view(rid)
                    with owner.lock:
                        # Contacts involving another entity or scene prop can support
                        # weight. Refuse normal deletion rather than teleporting it away.
                        for contact in owner.data.contact:
                            roots = [int(owner.model.body_rootid[owner.model.geom_bodyid[g]]) for g in contact.geom]
                            if view.base_body in roots and any(r not in (0, view.base_body) for r in roots):
                                raise ValueError('Robot is in contact with another entity')
                    roster = [r for r in roster if r['robot_instance_id'] != rid]
                result = owner.transact(roster, expected_revision=payload['expected_revision'])
                prepare_rendering(owner)
                if new_bus is not None:
                    buses[rid] = new_bus
                    new_bus = None
                    app.mount('/robots/' + rid, robot_app(owner.endpoint(rid)))
                else:
                    bus = buses.pop(rid, None)
                    if bus is not None:
                        bus.stop()
                return {'ok':True, 'topology_revision':owner.topology_revision, 'configuration':result}
            except (ValueError, RuntimeError) as error:
                return {'ok':False, 'message':str(error), 'topology_revision':owner.topology_revision}
            finally:
                if new_bus is not None:
                    new_bus.stop()

    try:
        prepare_rendering(owner)
        owner.start()
        for record in config['roster']:
            rid = record['robot_instance_id']
            view = owner.endpoint(rid)
            bus = BusServer(record['bus_url'], make_bus_handler(view))
            bus.start()
            buses[rid] = bus
            app.mount('/robots/' + rid, robot_app(view))
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, log_level='warning')
    finally:
        for bus in buses.values():
            bus.stop()
        owner.stop()


if __name__ == '__main__':
    main()
