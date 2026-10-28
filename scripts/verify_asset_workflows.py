"""Verify imported scene editing and export, optionally render with CPU EGL.

All outputs go to the explicitly supplied isolated home. Original packages stay
unchanged. This checks software loading, not robot control or task success.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def verify(packages, output, render=False):
    from nerv.asset_store import AssetStore, MANIFEST
    from nerv.install_downloads import digest
    from nerv.install_system import atomic_json
    from nerv.asset_runtime import inspect_scene
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new acceptance directory')
    output.mkdir(parents=True)
    if render:
        os.environ.update(MUJOCO_GL='egl', LIBGL_ALWAYS_SOFTWARE='1', CUDA_VISIBLE_DEVICES='-1')
        from nerv.world.rendering import configure_software_egl
        configure_software_egl()
    results = []
    library = AssetStore(output / 'home')
    for package in packages:
        original_hash = digest(package)
        first = library.import_asset(package)
        original, original_checked = library.resolve(first['id'])
        load = inspect_scene(original)
        copy = library.clone(first['id'], first['name'] + ' personal copy')
        workspace = Path(copy['workspace'])
        manifest = json.loads((workspace / MANIFEST).read_text())
        manifest['description'] = 'Personal revision created by the isolated acceptance check.'
        atomic_json(workspace / MANIFEST, manifest)
        # Change an actual scene property without altering a control contract.
        import xml.etree.ElementTree as ET
        scene = workspace / manifest['entry']
        tree = ET.parse(scene)
        geom = tree.find('.//worldbody/geom')
        if geom is None:
            raise ValueError('The acceptance scene requires a static world geometry')
        geom.set('rgba', '.3 .4 .5 1')
        tree.write(scene, encoding='unicode')
        saved = library.save(copy['id'])
        assert saved['current'] != copy['current']
        unchanged, _ = library.resolve(first['id'], first['current'])
        assert unchanged == original
        library.resolve(copy['id'], copy['current'])
        new, _ = library.resolve(copy['id'], saved['current'])
        copy_load = inspect_scene(new)
        exported = output / (Path(package).stem + '-personal.zip')
        library.export(copy['id'], exported)
        restored = AssetStore(output / 'reimport').import_asset(exported)
        assert restored['current'] == saved['current']
        assert digest(package) == original_hash
        result = {'package': Path(package).name, 'sha256': original_hash,
                  'source_revision': original_checked['revision'], 'personal_revision': saved['current'],
                  'load': load, 'personal_load': copy_load, 'roundtrip': 'passed', 'source_unchanged': True}
        if render:
            import mujoco
            import numpy as np
            from PIL import Image
            from nerv.asset_runtime import prepare_scene
            sim = prepare_scene(original, original_checked)
            model, data = sim.model, sim.data
            frames = []
            with mujoco.Renderer(model, height=480, width=640) as renderer:
                cameras = list(range(model.ncam)) if model.ncam else [-1]
                for camera in cameras:
                    renderer.update_scene(data, camera=camera)
                    pixels = renderer.render()
                    if not np.isfinite(pixels).all() or pixels.std() < 1:
                        raise ValueError('Camera produced an empty frame')
                    path = output / (Path(package).stem + f'-camera-{camera}.png')
                    Image.fromarray(pixels).save(path)
                    frames.append(path.name)
            result['render'] = {'backend': 'CPU EGL', 'frames': frames}
        results.append(result)
        atomic_json(output / 'report.json', {'scenes': results, 'task_performance': 'not_checked'})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('packages', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.packages, args.output, args.render), indent=2))
