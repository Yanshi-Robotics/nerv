from nerv.platform.registry import Registry
from nerv.platform.registry.compat import check, matrix


def test_source_installed_and_external_registries_use_one_asset_path(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from nerv import paths

    monkeypatch.setattr(paths, 'NERV_HOME', str(tmp_path / 'home'))
    registry = Registry(roots=[paths.REPO_ROOT])
    assert {'apt', 'house', 'so101-pick-place', 'so101-sort-snacks', 'so101-hanoi-five'} <= set(registry.worlds)
    for world in registry.worlds.values():
        assert Path(world.dir).is_relative_to(Path(paths.REPO_ROOT) / 'nerv-assets')
        assert Path(world.assets_root) == Path(paths.REPO_ROOT) / 'nerv-assets'
        assert (Path(world.assets_root) / 'scenes/manifest.py').is_file()

    external = tmp_path / 'module'
    descriptor = external / 'nerv-assets/example/world.yaml'
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(json.dumps({'kind': 'sim', 'engine': 'mujoco'}))
    assert set(Registry(roots=[external]).worlds) == {'example'}
    monkeypatch.setattr(paths, '_IN_CHECKOUT', False)
    monkeypatch.setattr(paths, 'REPO_ROOT', str(external))
    assert set(Registry(roots=[external]).worlds) == {'example'}


def test_legacy_asset_registry_is_rejected(tmp_path, monkeypatch):
    import pytest
    from nerv import paths
    monkeypatch.setattr(paths, '_IN_CHECKOUT', True)
    monkeypatch.setattr(paths, 'REPO_ROOT', str(tmp_path))
    monkeypatch.setattr(paths, 'NERV_HOME', str(tmp_path / 'home'))
    stale = tmp_path / 'worlds/obsolete/world.yaml'
    stale.parent.mkdir(parents=True)
    stale.write_text('kind: sim\nengine: mujoco\n')
    with pytest.raises(ValueError, match="Legacy worlds/"):
        Registry(roots=[tmp_path])


def test_repo_registry_loads():
    reg = Registry()
    assert {"arm-lerobot-so101", "humanoid-unitree-g1"} <= set(reg.bodies)
    assert {"apt", "house"} <= set(reg.worlds) and "calculator" in reg.tools
    assert not {"apt1", "apt2", "house1", "house2"} & set(reg.worlds)
    assert reg.bodies["humanoid-unitree-g1"].family == "humanoid"
    assert reg.bodies["humanoid-unitree-g1"].guidance      # guidance.md picked up


def test_apt_supports_only_g1():
    reg = Registry()
    ok, reason = check(reg.worlds["apt"], reg.bodies["humanoid-unitree-g1"])
    assert not ok and 'Import and check policy' in reason
    # Geometry compatibility is independent of whether this user imported a policy.
    assert check(reg.worlds['apt'], reg.bodies['humanoid-unitree-g1'].model_copy(update={'policy_error': ''}))[0]
    ok, reason = check(reg.worlds["apt"], reg.bodies["arm-lerobot-so101"])
    assert not ok and "no arena" in reason
    rows = matrix(reg)
    assert any(r["world"] == "apt" and r["body"] == "arm-lerobot-so101" and not r["ok"] for r in rows)


def test_hub_refuses_incompatible_session(tmp_path):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    hub = Nerv(store=SessionStore(str(tmp_path / "s")))
    from types import SimpleNamespace
    hub._brains["mock"] = SimpleNamespace(model="offline-fixture")
    try:
        hub.new_session("mock", "arm-lerobot-so101", "apt")
    except ValueError as e:
        assert "no arena" in str(e)
    else:
        raise AssertionError("expected refusal")
    assert hub.store.list() == []          # nothing was created
