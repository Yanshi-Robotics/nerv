"""Keep the installed numerical stack identical to measured control qualification."""
import copy
import runpy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HELPERS = runpy.run_path(str(ROOT / 'scripts/build_download_plans.py'))


def test_release_simulation_dependencies_match_qualified_control():
    versions = HELPERS['qualified_sim_dependencies'](ROOT)
    assert set(versions) == {'mujoco', 'numpy', 'onnxruntime'}


def test_conflicting_installation_constraint_is_rejected_before_resolution(tmp_path):
    profile = {'buses': {'sim': {'settings': {'skill_transitions': {
        'validated': True, 'dependency_versions': {'mujoco': '3.12.0', 'numpy': '2.5.2', 'onnxruntime': '1.31.0'}}}}}}
    body = tmp_path / 'bodies/fixture/body.yaml'
    body.parent.mkdir(parents=True)
    body.write_text(yaml.safe_dump(profile))
    pins = 'mujoco==3.12.0\nnumpy==2.5.2\nonnxruntime==1.31.0\n'
    for relative in ('release/constraints/sim.txt', 'release/validation.in', 'release/validation.lock'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pins)
    assert HELPERS['qualified_sim_dependencies'](tmp_path)['numpy'] == '2.5.2'
    (tmp_path / 'release/constraints/sim.txt').write_text(pins.replace('numpy==2.5.2', 'numpy==2.5.4'))
    with pytest.raises(ValueError, match='numpy must match qualified simulation version 2.5.2'):
        HELPERS['qualified_sim_dependencies'](tmp_path)
    for invalid in (pins + 'numpy==2.5.2\n', pins.replace('numpy==2.5.2', 'numpy==2.5.2; sys_platform == "linux"')):
        (tmp_path / 'release/constraints/sim.txt').write_text(invalid)
        with pytest.raises(ValueError, match='one unconditional pin'):
            HELPERS['qualified_sim_dependencies'](tmp_path)


def test_resolved_wheels_cannot_silently_change_the_numerical_stack():
    versions = {'mujoco': '3.12.0', 'numpy': '2.5.2', 'onnxruntime': '1.31.0'}
    plan = {'artifacts': [{'name': f'{name}-{version}-cp312-cp312-manylinux_2_28_x86_64.whl', 'kind': 'wheel'}
                          for name, version in versions.items()]}
    HELPERS['verify_qualified_artifacts'](plan, versions)
    changed = copy.deepcopy(plan)
    changed['artifacts'][1]['name'] = changed['artifacts'][1]['name'].replace('2.5.2', '2.5.4')
    with pytest.raises(ValueError, match='differ from the control qualification'):
        HELPERS['verify_qualified_artifacts'](changed, versions)
    with pytest.raises(ValueError, match='differ from the control qualification'):
        HELPERS['verify_qualified_artifacts']({'artifacts': []}, versions)
