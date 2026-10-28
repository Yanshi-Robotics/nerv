"""Immutable records for the existing data-only ONNX motion-policy contract.

Import references external files without rewriting them. A record is not a license
grant or a claim of robot compatibility; loading and binding are separate operations.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from filelock import FileLock
import yaml

from .asset_store import identifier
from .install_downloads import digest
from .install_system import atomic_json
from .managed import fingerprint

FILES = ('policy.onnx', 'contract.json', 'release.yaml')


def inventory(path):
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Select an ONNX policy directory')
    files = {}
    for name in FILES:
        target = root / name
        if target.is_symlink() or not target.is_file():
            raise ValueError('Policy requires a regular file: ' + name)
        files[name] = digest(target)
    contract = json.loads((root / 'contract.json').read_text())
    release = yaml.safe_load((root / 'release.yaml').read_text())
    if not isinstance(contract, dict) or not isinstance(release, dict):
        raise ValueError('Policy contract and release must be objects')
    alternate = root / 'contract.yaml'
    if alternate.exists():
        if alternate.is_symlink() or yaml.safe_load(alternate.read_text()) != contract:
            raise ValueError('JSON and YAML policy contracts differ; resolve the source ambiguity first')
        files['contract.yaml'] = digest(alternate)
    expected = release.get('files', {}).get('policy.onnx', {}).get('sha256')
    if expected and expected != files['policy.onnx']:
        raise ValueError('Policy weight does not match the release checksum')
    for key in ('policy_id', 'robot', 'family'):
        if not isinstance(release.get(key), str) or not release[key].strip():
            raise ValueError('Release is missing ' + key)
    return root, files, contract, release


def import_policy(home, key, path, conditions=''):
    identifier(key)
    root, files, _, release = inventory(path)
    record = {'schema': 1, 'id': key, 'format': 'onnx-motion', 'path': str(root),
              'files': files, 'robot': release['robot'], 'family': release['family'],
              'source_policy_id': release['policy_id'], 'conditions': conditions}
    revision = fingerprint(record)
    record['revision'] = revision
    directory = Path(home) / 'motion-policies' / key
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / 'write.lock')):
        target = directory / (revision + '.json')
        if target.exists() and json.loads(target.read_text()) != record:
            raise ValueError('Existing policy revision differs from its identity')
        atomic_json(target, record)
        atomic_json(directory / 'current.json', {'revision': revision})
    return record


def inspect(home, key, revision=None):
    identifier(key)
    directory = Path(home) / 'motion-policies' / key
    revision = revision or json.loads((directory / 'current.json').read_text())['revision']
    if not isinstance(revision, str) or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Invalid policy revision')
    record = json.loads((directory / (revision + '.json')).read_text())
    if record.get('revision') != revision or fingerprint({k: v for k, v in record.items() if k != 'revision'}) != revision:
        raise ValueError('Policy record identity changed')
    _, files, _, _ = inventory(record['path'])
    if files != record['files']:
        raise ValueError('Policy source files changed; import a new revision')
    return record


def validate_contract(contract, release):
    """Require the semantics actually consumed by the current humanoid controller."""
    if release['family'] != 'humanoid':
        raise ValueError('This ONNX family has no installed motion controller')
    names = contract.get('joint_names')
    if not isinstance(names, list) or not names or len(set(names)) != len(names) or not all(isinstance(v, str) and v for v in names):
        raise ValueError('Policy requires unique joint names')
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    history = contract.get('history_length', 1)
    if type(history) is not int or history < 1:
        raise ValueError('Policy history length must be a positive integer')
    scales = contract.get('term_scales')
    if not isinstance(scales, dict) or not scales or not all(finite(v) for v in scales.values()):
        raise ValueError('Policy observation scales must be finite')
    pose = contract.get('default_joint_pos')
    if not isinstance(pose, list) or len(pose) != len(names) or not all(finite(v) for v in pose):
        raise ValueError('Policy default pose does not match its joints')
    for key in ('lin_cmd_deadband', 'turn_vx'):
        if not finite(release.get(key)):
            raise ValueError('Policy deployment semantics are missing: ' + key)
    ranges = release.get('command_ranges')
    if not isinstance(ranges, dict) or set(ranges) != {'vx', 'vy', 'wz'}:
        raise ValueError('Policy requires explicit velocity command ranges')
    for values in ranges.values():
        if not isinstance(values, (list, tuple)) or len(values) != 2 or not all(finite(v) for v in values) or values[0] > 0 or values[1] < 0:
            raise ValueError('Policy command ranges must be finite and include rest')
    if release['lin_cmd_deadband'] < 0 or not ranges['vx'][0] <= release['turn_vx'] <= ranges['vx'][1]:
        raise ValueError('Policy command defaults are outside the declared ranges')
    for value in (contract.get('action', {}).get('scale'), contract.get('timing', {}).get('policy_dt_s')):
        if not finite(value) or value <= 0:
            raise ValueError('Policy action scale and control period must be positive')


def check(home, key, revision=None):
    """Run only in the selected simulation environment; never open hardware."""
    import numpy as np
    import onnxruntime as ort
    ort.disable_telemetry_events()
    from .body.families.humanoid import PolicyRelease, TERM_ORDER
    record = inspect(home, key, revision)
    root, _, contract, release = inventory(record['path'])
    validate_contract(contract, release)
    policy = PolicyRelease(str(root))
    sizes = {'base_ang_vel': 3, 'projected_gravity': 3,
             'velocity_commands': 3, 'joint_pos_rel': len(policy.joint_names),
             'joint_vel_rel': len(policy.joint_names), 'last_action': len(policy.joint_names)}
    if set(policy.term_order) - set(sizes) or set(policy.term_order) - set(TERM_ORDER):
        raise ValueError('Unsupported observation contract')
    dimension = sum(sizes[t] for t in policy.term_order) * policy.history
    if policy.obs_dim != dimension or policy.history < 1:
        raise ValueError('Policy observation dimensions do not match the controller')
    for values in (policy.kp, policy.kd, policy.torque_limit):
        if len(values) != len(policy.joint_names) or not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError('Policy requires finite actuator limits and gains')
    if len(policy._sess.get_inputs()) != 1 or len(policy._sess.get_outputs()) != 1:
        raise ValueError('Motion policies require one observation input and one action output')
    if len(set(policy.term_order)) != len(policy.term_order) or set(policy.term_order) - set(policy.scales):
        raise ValueError('Observation terms must be unique and have declared scales')
    for input_ in policy._sess.get_inputs():
        if input_.name != policy._in or input_.type != 'tensor(float)' or len(input_.shape) != 2 or input_.shape[1] != dimension:
            raise ValueError('ONNX input differs from the motion contract')
    output = policy.act(np.zeros(dimension, dtype=np.float32))
    if output.shape != (len(policy.joint_names),) or not np.isfinite(output).all():
        raise ValueError('ONNX policy returned invalid action dimensions or values')
    inspect(home, key, record['revision'])
    result = {'state': 'loaded', 'revision': record['revision'], 'provider': 'CPUExecutionProvider',
              'observation_dim': dimension, 'action_dim': len(policy.joint_names),
              'motion': False, 'task_success': None}
    atomic_json(Path(home) / 'motion-policies' / key / ('check-' + record['revision'] + '.json'), result)
    return result


def manage(home, operation, payload, python=''):
    if operation == 'import':
        return import_policy(home, payload['id'], payload['path'], payload.get('conditions', ''))
    if operation == 'inspect':
        return inspect(home, payload['id'], payload.get('revision'))
    if operation == 'select':
        record = inspect(home, payload['id'], payload.get('revision'))
        record = resolve_binding(home, record['id'], record['family'], record['robot'], record['revision'])
        target = binding_path(home, record['family'], record['robot'])
        target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(target) + '.lock'):
            atomic_json(target, {'id': record['id'], 'revision': record['revision']})
        return record
    if operation == 'policies':
        result = []
        for path in sorted((Path(home) / 'motion-policies').glob('*/current.json')):
            try:
                record = inspect(home, path.parent.name)
                receipt = path.parent / ('check-' + record['revision'] + '.json')
                record['check'] = json.loads(receipt.read_text()) if receipt.exists() else {'state': 'unchecked'}
                binding = binding_path(home, record['family'], record['robot'])
                selected = json.loads(binding.read_text()) if binding.exists() else {}
                record['selected'] = selected == {'id': record['id'], 'revision': record['revision']}
                result.append(record)
            except (OSError, ValueError, KeyError) as error:
                result.append({'id': path.parent.name, 'check': {'state': 'changed'}, 'error': str(error)})
        return {'policies': result}
    if operation != 'check':
        raise ValueError('Unsupported ONNX policy operation')
    from . import managed
    from .platform.runtime_management import environment, check_interpreter
    executable = Path(python).absolute()
    if not executable.is_file():
        raise ValueError('Install and select a compatible simulation environment')
    record = inspect(home, payload['id'], payload.get('revision'))
    check_interpreter(executable)
    process = managed.run(managed.storage_root(home), [executable.parent.parent, record['path']],
        [str(executable), '-m', 'nerv.motion_policies', '--home', str(home), '--id', record['id'],
         '--revision', record['revision']], env=environment(), capture_output=True, text=True, timeout=180)
    if process.returncode:
        raise ValueError(process.stderr.strip().splitlines()[-1] if process.stderr.strip() else 'Policy loading failed')
    return json.loads(process.stdout.strip().splitlines()[-1])


def binding_path(home, family, robot):
    return Path(home) / 'motion-bindings' / (fingerprint({'family': family, 'robot': robot}) + '.json')


def selected_binding(home, default_key, family, robot):
    target = binding_path(home, family, robot)
    if target.exists():
        value = json.loads(target.read_text())
        return resolve_binding(home, value['id'], family, robot, value['revision'])
    return resolve_binding(home, default_key, family, robot)


def resolve_binding(home, key, family, robot, revision=None):
    record = inspect(home, key, revision)
    if record['family'] != family or record['robot'] != robot:
        raise ValueError('Imported policy targets a different robot contract')
    receipt = Path(home) / 'motion-policies' / key / ('check-' + record['revision'] + '.json')
    checked = json.loads(receipt.read_text()) if receipt.exists() else {}
    if checked.get('state') != 'loaded' or checked.get('revision') != record['revision']:
        raise ValueError('Run the no-motion policy check before selecting this policy')
    return record


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--revision')
    args = parser.parse_args()
    print(json.dumps(check(args.home, args.id, args.revision)))
