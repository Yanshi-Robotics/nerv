"""Translate data-only asset bindings into installed simulation capabilities.

An asset cannot select an interpreter, import a builder or change a real binding.
World identities include the complete saved revision, so updates affect new sessions.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool

from .asset_store import AssetStore, identifier


class Spawn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    initial_joints: dict[str, FiniteFloat] = Field(default_factory=dict)
    x: FiniteFloat = 0
    y: FiniteFloat = 0
    yaw: FiniteFloat = 0


class Physics(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dt: FiniteFloat = Field(default=0.002, gt=0, le=0.1)
    realtime_factor: FiniteFloat = Field(default=1, gt=0, le=10)
    render_width: int = Field(default=640, ge=16, le=2048, strict=True)
    render_height: int = Field(default=480, ge=16, le=2048, strict=True)
    stream_fps: FiniteFloat = Field(default=12, gt=0, le=60)
    third_person: StrictBool = True
    chase_back_m: FiniteFloat = Field(default=2.2, gt=0, le=100)
    chase_up_m: FiniteFloat = Field(default=1.3, gt=0, le=100)
    chase_min_m: FiniteFloat = Field(default=0.2, gt=0, le=100)


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bodies: list[str] = Field(min_length=1, max_length=32)
    spawn: Spawn = Field(default_factory=Spawn)
    physics: Physics = Field(default_factory=Physics)


def binding(manifest):
    value = manifest.get('runtime')
    if value is None:
        return None
    if manifest['kind'] != 'scene':
        raise ValueError('Only scene assets may declare a simulation binding')
    result = Binding.model_validate(value)
    for name in result.bodies:
        identifier(name)
    if len(set(result.bodies)) != len(result.bodies):
        raise ValueError('Duplicate robot binding')
    return result


def world_name(key, revision):
    return f'asset-{key}-{revision}'


def worlds(home, bodies):
    """Keep old revisions addressable, but only offer current ones for new sessions."""
    from .platform.registry.schema import WorldSpec
    store = AssetStore(home)
    result, errors = {}, []
    for record in store.list():
        for revision in record['revisions']:
            try:
                directory, checked = store.resolve(record['id'], revision)
                manifest = checked['manifest']
                spec = binding(manifest)
                if spec is None:
                    continue
                supported = {name: bodies[name] for name in spec.bodies if name in bodies
                             and bodies[name].buses.get('sim') and bodies[name].buses['sim'].enabled}
                if not supported:
                    raise ValueError('Install a compatible simulation runtime for this asset')
                interpreters = {body.buses['sim'].python for body in supported.values()}
                if len(interpreters) != 1:
                    raise ValueError('Asset bindings require different simulation environments')
                name = world_name(record['id'], revision)
                result[name] = WorldSpec(name=name, label=manifest['name'], kind='sim', engine='mujoco',
                    assets_root=str(directory), supports={name: manifest['entry'] for name in supported},
                    spawn=spec.spawn.model_dump(), physics=spec.physics.model_dump(),
                    python=interpreters.pop(), dir=str(directory),
                    asset={'id': record['id'], 'revision': revision, 'current': record['current'] == revision})
            except (ValueError, OSError, KeyError) as error:
                errors.append({'id': record['id'], 'revision': revision, 'reason': str(error)})
    return result, errors


def check(home, key, revision, bodies):
    """Explicit CPU-only load check in the installed simulation interpreter."""
    from . import managed
    from .install_system import atomic_json
    from .platform.runtime_management import environment, check_interpreter
    store = AssetStore(home)
    directory, checked = store.resolve(key, revision)
    registered, errors = worlds(home, bodies)
    name = world_name(key, checked['revision'])
    if name not in registered:
        reason = next((item['reason'] for item in errors if item['id'] == key
                       and item['revision'] == checked['revision']), 'The asset needs a scene runtime binding')
        raise ValueError(reason)
    world = registered[name]
    if not world.python:
        raise ValueError('Install a simulation environment before checking this asset')
    python = Path(world.python).absolute()
    env = environment()
    env.update(NERV_HOME=str(home), NERV_STORAGE_HOME=str(managed.storage_root(home)),
               CUDA_VISIBLE_DEVICES='-1', PYTHONDONTWRITEBYTECODE='1')
    with managed.lease(managed.storage_root(home), [directory, python.parent.parent], child=True) as reservation:
        try:
            check_interpreter(str(python))
            proc = subprocess.Popen([str(python), '-m', 'nerv.asset_runtime', '--check', str(directory)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding='utf-8', env=env)
        except BaseException:
            reservation.failed_to_start()
            raise
        reservation.started(proc.pid)
        try:
            output, error = proc.communicate(timeout=180)
        except subprocess.TimeoutExpired as error:
            proc.kill()
            proc.communicate()
            raise ValueError('Simulation load check timed out; inspect the scene complexity and retry.') from error
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        if proc.returncode:
            raise ValueError(error.strip()[-2000:] or 'Simulation asset check failed')
        result = json.loads(output.strip().splitlines()[-1])
        # Reject mutation during a check instead of certifying different bytes.
        store.resolve(key, checked['revision'])
        result.update(revision=checked['revision'], python=str(python))
        atomic_json(store.root / 'checks' / key / (checked['revision'] + '.json'), result)
        return result


def prepare_scene(directory, checked=None):
    """Load the declared pose for checks/previews without starting a controller."""
    import math
    import mujoco
    from .asset_store import validate
    from .world.mujoco_node import WorldSim
    checked = checked or validate(directory)
    spec = binding(checked['manifest'])
    if spec is None:
        raise ValueError('The asset has no simulation binding')
    sim = WorldSim(str(Path(directory) / checked['manifest']['entry']),
                   physics=spec.physics.model_dump(), spawn=spec.spawn.model_dump())
    sim._reset_data()
    if sim.has_free_base:
        # Match the data-only x/y/yaw spawn contract. Root height stays in MJCF.
        a = sim.free_qadr
        sim.data.qpos[a:a + 2] = [spec.spawn.x, spec.spawn.y]
        sim.data.qpos[a + 3:a + 7] = [math.cos(spec.spawn.yaw / 2), 0, 0, math.sin(spec.spawn.yaw / 2)]
    mujoco.mj_forward(sim.model, sim.data)
    return sim


def inspect_scene(directory):
    """Load and step data without opening cameras, networks or a real robot."""
    import mujoco
    import numpy as np
    sim = prepare_scene(directory)
    mujoco.mj_step(sim.model, sim.data, nstep=10)
    if not all(np.isfinite(value).all() for value in (sim.data.qpos, sim.data.qvel, sim.data.qacc)):
        raise ValueError('Scene produced non-finite physics state')
    if any(warning.number for warning in sim.data.warning):
        raise ValueError('Scene produced a physics warning during the load check')
    return {'runtime': 'passed', 'task': 'not_checked', 'rendering': 'not_checked',
            'engine_version': mujoco.__version__, 'steps': 10,
            'dimensions': {key: int(getattr(sim.model, key)) for key in ('nq', 'nv', 'nu', 'nbody')}}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', type=Path, required=True)
    print(json.dumps(inspect_scene(parser.parse_args().check)))
