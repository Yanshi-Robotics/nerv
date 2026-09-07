from nerv.platform.registry import Registry
from nerv.platform.registry.compat import check, matrix


def test_repo_registry_loads():
    reg = Registry()
    assert {"arm-lerobot-so101", "humanoid-unitree-g1"} <= set(reg.bodies)
    assert {"apt", "house"} <= set(reg.worlds) and "calculator" in reg.tools
    assert not {"apt1", "apt2", "house1", "house2"} & set(reg.worlds)
    assert reg.bodies["humanoid-unitree-g1"].family == "humanoid"
    assert reg.bodies["humanoid-unitree-g1"].guidance      # guidance.md picked up


def test_apt_supports_only_g1():
    reg = Registry()
    ok, _ = check(reg.worlds["apt"], reg.bodies["humanoid-unitree-g1"])
    assert ok
    ok, reason = check(reg.worlds["apt"], reg.bodies["arm-lerobot-so101"])
    assert not ok and "no arena" in reason
    rows = matrix(reg)
    assert any(r["world"] == "apt" and r["body"] == "arm-lerobot-so101" and not r["ok"] for r in rows)


def test_hub_refuses_incompatible_session(tmp_path):
    from nerv.platform.hub import Nerv
    from nerv.platform.session import SessionStore
    hub = Nerv(store=SessionStore(str(tmp_path / "s")))
    try:
        hub.new_session("mock", "arm-lerobot-so101", "apt")
    except ValueError as e:
        assert "no arena" in str(e)
    else:
        raise AssertionError("expected refusal")
    assert hub.store.list() == []          # nothing was created
