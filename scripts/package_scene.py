"""Package a reviewed MJCF source with declared licenses; no publishing or rendering.

Includes are expanded without serializing floating-point values through the engine.
Referenced bytes are copied, then the compiled models are compared.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def pack(root, recipe, destination):
    import mujoco
    import numpy as np
    from nerv.asset_store import FILE_ATTRIBUTES, MANIFEST, relative, validate
    from nerv.install_downloads import digest

    root, destination = Path(root).resolve(strict=True), Path(destination).absolute()
    entry = root / str(relative(recipe['source']))
    if not entry.resolve().is_relative_to(root):
        raise ValueError('Scene source must belong to its declared source root')
    if destination.exists():
        raise ValueError('Choose a new package destination')
    declarations = recipe['licenses']
    sources = {}

    def evidence(path):
        path = path.resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError('Scene dependency escapes the source root')
        name = path.relative_to(root).as_posix()
        matches = [v for v in declarations if any(fnmatch.fnmatchcase(name, pattern) for pattern in v['files'])]
        if len(matches) != 1:
            raise ValueError('Each source file needs one license declaration: ' + name)
        declaration = matches[0]
        if not isinstance(declaration.get('license'), str) or not declaration['license']:
            raise ValueError('Missing source license: ' + name)
        sources[name] = {'sha256': digest(path), 'license': declaration['license']}
        if declaration.get('source'):
            sources[name]['source'] = declaration['source']
        return path

    seen = set()
    def inspect_xml(path):
        path = evidence(path)
        if path in seen:
            raise ValueError('Duplicate or recursive XML include')
        seen.add(path)
        raw = path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('External XML declarations are not supported')
        document = ET.fromstring(raw)
        for node in document.iter():
            if node.tag in ('plugin', 'extension'):
                raise ValueError('Native plugins are not supported by data packages')
        for parent in list(document.iter()):
            for node in list(parent):
                if node.tag == 'include':
                    included = inspect_xml(path.parent / node.attrib['file'])
                    offset = list(parent).index(node)
                    parent.remove(node)
                    for index, child in enumerate(list(included)):
                        parent.insert(offset + index, child)
        return document
    tree = inspect_xml(entry)
    spec = mujoco.MjSpec.from_file(str(entry))
    original = spec.compile()
    if any(node.tag in ('plugin', 'extension', 'include') for node in tree.iter()):
        raise ValueError('Export must be flattened and cannot load native plugins')
    dirs = {'meshdir': spec.meshdir, 'texturedir': spec.texturedir}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.scene-') as temporary:
        stage = Path(temporary)
        for node in tree.iter():
            for attribute in FILE_ATTRIBUTES:
                value = node.get(attribute)
                if not value:
                    continue
                directory = dirs['meshdir'] if node.tag == 'mesh' else dirs['texturedir'] if node.tag == 'texture' else ''
                source = evidence(entry.parent / directory / value)
                target = stage / 'resources' / digest(source) / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                node.set(attribute, target.relative_to(stage).as_posix())
        for compiler in tree.iter('compiler'):
            for key in ('meshdir', 'texturedir', 'assetdir'):
                compiler.attrib.pop(key, None)
            compiler.set('strippath', 'false')
        tree_path = stage / 'scene.xml'
        ET.ElementTree(tree).write(tree_path, encoding='utf-8', xml_declaration=True)
        for declaration in declarations:
            notice = root / str(relative(declaration['notice']))
            if not notice.resolve(strict=True).is_relative_to(root):
                raise ValueError('License notice escapes the source root')
            content = notice.read_bytes()
            # Different authors can use the same license. Preserve each notice.
            identity = hashlib.sha256(declaration['license'].encode() + b'\0' + content).hexdigest()
            target = stage / 'licenses' / (identity + '.txt')
            target.parent.mkdir(exist_ok=True)
            if target.exists() and target.read_bytes() != content:
                raise ValueError('Conflicting license notices')
            target.write_bytes(content)
        manifest = {**recipe['manifest'], 'schema': 1, 'engine': 'mujoco', 'entry': 'scene.xml'}
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n')
        (stage / 'sources.json').write_text(json.dumps(sources, indent=2) + '\n')
        checked = validate(stage)
        restored = mujoco.MjModel.from_xml_path(str(tree_path))
        # Compare the compiled physics, not only dimensions or file existence.
        fields = ('nq', 'nv', 'nu', 'nbody', 'ngeom', 'nmesh', 'ntex')
        if any(getattr(original, key) != getattr(restored, key) for key in fields):
            raise ValueError('Packaged model dimensions differ from the source')
        for key in ('body_mass', 'body_inertia', 'body_pos', 'body_quat', 'geom_pos', 'geom_quat',
                    'geom_size', 'jnt_range', 'actuator_ctrlrange', 'qpos0'):
            if not np.allclose(getattr(original, key), getattr(restored, key), rtol=1e-6, atol=1e-8):
                raise ValueError('Packaged model changed: ' + key)
        with destination.open('xb') as output, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name in sorted(checked['files']):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, (stage / name).read_bytes())
    return {'path': str(destination), 'sha256': digest(destination), 'bytes': destination.stat().st_size,
            'revision': checked['revision'], 'model_dimensions': {key: getattr(restored, key) for key in fields},
            'validation': {'files': 'passed', 'model_load': 'passed', 'compiled_physics': 'passed',
                           'rendering': 'not_checked', 'task': 'not_checked'}}


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(pack(args.root, json.loads(args.recipe.read_text()), args.output), indent=2))
