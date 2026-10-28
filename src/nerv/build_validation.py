"""Validate a built candidate in a separate, disposable managed environment."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import managed
from .installation import python_at
from .install_system import atomic_json

CONTRACT_TESTS = [
    'test_cli_serve.py', 'test_shutdown_process.py', 'test_user_preferences.py',
    'test_standard_product.py', 'test_lerobot_adapter.py', 'test_runtime_sdk.py',
    'test_runtime_management.py', 'test_user_paths.py', 'test_control_schemes.py',
    'test_installation.py', 'test_release_recovery.py', 'test_release_wheel.py', 'test_setup_discovery.py',
    'test_harness_memory.py', 'test_environment_cleanup.py', 'test_managed.py',
    'test_asset_store.py', 'test_asset_catalog.py', 'test_asset_runtime.py',
    'test_policy_sources.py', 'test_build_workspace.py', 'test_managed_preview.py', 'test_recoverable_downloads.py',
    'test_scene_package.py', 'test_simulation_module.py', 'test_motion_policies.py', 'test_motion_policy_api.py', 'test_build_retention.py',
]


def validate(directory, storage, tools, env, log, *, smoke=False):
    directory, storage = Path(directory), Path(storage)
    source, bundle = directory / 'source', directory / 'bundle'
    python = python_at(directory / 'validation')
    uv = Path(tools['python']).parent / ('uv.exe' if os.name == 'nt' else 'uv')
    env = dict(env, PYTHONDONTWRITEBYTECODE='1', CUDA_VISIBLE_DEVICES='-1',
               NERV_HOME=str(directory / 'validation-home'), NERV_STORAGE_HOME=str(directory / 'validation-home'))
    if sys.platform == 'linux':
        env.update(MUJOCO_GL='egl', LIBGL_ALWAYS_SOFTWARE='1')
    state = {'state': 'running', 'checks': [], 'hardware': 'not_tested'}
    report = directory / 'validation.json'
    atomic_json(report, state)
    def run(name, command, cwd=source, timeout=900):
        managed.run(storage, [directory, Path(tools['python']).parent.parent, Path(tools['node']).parent],
                    command, cwd=cwd, env=env, stdout=log, stderr=log, timeout=timeout, check=True)
        state['checks'].append(name)
        atomic_json(report, state)
    try:
        run('validation environment', [str(uv), 'venv', '--python', tools['python'], str(python.parent.parent)])
        run('locked validation dependencies', [str(uv), 'pip', 'install', '--python', str(python),
            '--require-hashes', '--find-links', str(bundle), '-r', str(bundle / 'host.lock'),
            '-r', str(source / 'release/validation.lock')])
        wheels = [str(p) for p in bundle.glob('nerv_runtime_lerobot-*.whl')]
        if len(wheels) != 1:
            raise ValueError('Candidate must contain exactly one device adapter wheel')
        run('adapter fixture', [str(uv), 'pip', 'install', '--python', str(python), '--no-deps', *wheels])
        run('frontend tests', [tools['node'], tools['npm'], 'test'], cwd=source / 'frontend')
        run('translations', [tools['node'], tools['npm'], 'run', 'check:locales'], cwd=source / 'frontend')
        run('frontend types', [tools['node'], str(source / 'frontend/node_modules/typescript/bin/tsc'),
                              '--noEmit', '--incremental', 'false'], cwd=source / 'frontend')
        run('launcher tests', [tools['node'], tools['npm'], 'test'])
        if sys.platform == 'linux':
            from .build_assets import prepare
            state['data_fixtures'] = prepare(source, storage, env)
            atomic_json(report, state)
            # Source-dependent simulation regressions need a coherent editable
            # snapshot, not PYTHONPATH over a differently packaged installation.
            run('managed source test installation', [str(uv), 'pip', 'install', '--python', str(python),
                '--no-deps', '--no-build-isolation', '-e', str(source)])
            run('complete CPU software and simulation suite', [str(python), '-m', 'pytest',
                '-p', 'no:cacheprovider', '-q', 'tests'])
        else:
            run('software contracts', [str(python), '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                                       *['tests/' + name for name in CONTRACT_TESTS]])
        if smoke:
            run('installed host and recovery', [str(python), str(source / 'scripts/verify_release.py'),
                '--manifest', str(bundle / 'release.json'), '--home', str(directory / 'release-smoke')], timeout=1800)
        state['state'] = 'passed'
        atomic_json(report, state)
        return state
    except BaseException:
        state['state'] = 'failed'
        atomic_json(report, state)
        raise
