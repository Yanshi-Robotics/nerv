"""Process entry point for an operator-configured shared physical World."""
import argparse
import json
import os

from fastapi import FastAPI

from .composition import SharedWorld
from .mujoco_node import build_app, make_bus_handler
from .bus_server import BusServer


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
    app = FastAPI(title='NERV shared World')
    buses = []

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

    try:
        owner.start()
        for record in config['roster']:
            rid = record['robot_instance_id']
            view = owner.view(rid)
            bus = BusServer(record['bus_url'], make_bus_handler(view))
            bus.start()
            buses.append(bus)
            app.mount('/robots/' + rid, robot_app(view))
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, log_level='warning')
    finally:
        for bus in buses:
            bus.stop()
        owner.stop()


if __name__ == '__main__':
    main()
