"""Cached wheels and differing build-host modes must not multiply environments."""
import hashlib
import importlib.util
from pathlib import Path
import zipfile

spec = importlib.util.spec_from_file_location('bundle_release', Path(__file__).parents[1] / 'scripts/bundle_release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_simulation_bundle_preserves_managed_motion_and_excludes_real_act():
    import yaml
    root = Path(__file__).parents[1]
    g1 = yaml.safe_load((root / 'bodies/humanoid-unitree-g1/body.yaml').read_text())
    arm = yaml.safe_load((root / 'bodies/arm-lerobot-so101/body.yaml').read_text())
    packaged = module.simulation_profile(g1)
    assert packaged['skills'] == g1['skills']
    assert all(skill['policy'].startswith('managed:') for skill in packaged['skills'].values())
    assert set(packaged['buses']) == {'sim'}
    assert module.simulation_profile(arm)['skills'] == {}
    assert 'real' in arm['buses']
    assert arm['skills']['snack_into_box']['modes'] == ['real']


def test_simulation_profile_respects_declared_modes_without_mutating_source():
    source = {'buses': {'sim': {}, 'real': {}}, 'skills': {
        'sim': {'modes': ['sim']}, 'both': {}, 'disabled': {'modes': []}}}
    packaged = module.simulation_profile(source)
    assert set(packaged['skills']) == {'sim', 'both'}
    packaged['skills']['sim']['modes'].append('real')
    assert source['skills']['sim']['modes'] == ['sim']


def test_identical_payloads_reuse_identity_across_wheel_build_metadata(tmp_path):
    from nerv.managed import environment_identity
    digests = []
    payload = {'package/main.py': b'print("fixture")\n', 'package.dist-info/RECORD': b'record\n'}
    for index, names in enumerate((list(payload), list(reversed(payload)))):
        path = tmp_path / f'{index}.whl'
        with zipfile.ZipFile(path, 'w') as wheel:
            for name in names:
                info = zipfile.ZipInfo(name, date_time=(2020 + index, 1, 1, 0, 0, 0))
                info.create_system = index * 3
                info.external_attr = (0o100600 + index * 0o44) << 16
                wheel.writestr(info, payload[name])
        module.canonical_wheel(path, path)
        with zipfile.ZipFile(path) as wheel:
            assert {name: wheel.read(name) for name in wheel.namelist()} == payload
        digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
    assert digests[0] == digests[1]
    plans = [{'target': 'linux-x86_64', 'backend': 'cpu', 'artifacts': [
        {'kind': 'wheel', 'name': 'fixture.whl', 'sha256': digest}]} for digest in digests]
    assert environment_identity('runtime', {}, {}, plans[0]) == environment_identity('runtime', {}, {}, plans[1])
