"""World control contract and real MJCF generation, without GPU or model calls."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import mujoco
import pytest
from jsonschema import ValidationError

from nerv.platform.registry.loader import Registry
from nerv.platform.world_controls import WorldControls
from nerv.world.configuration import build_instance, resolve_configuration
from nerv.world.control import WorldController


def test_instance_assets_use_selected_physics_and_keep_defaults(tmp_path):
    world = Registry().world("so101-pick-place")
    shared = Path(world.assets_root) / world.supports["arm-lerobot-so101"]
    before = shared.read_bytes()
    fingerprints = set()
    counts = {}
    for object_type in ("snack_rigid", "soft_snack", "cloth"):
        directory = tmp_path / object_type
        directory.mkdir()
        request = directory / "configuration.json"
        request.write_text(json.dumps({"values": {"snack": object_type}}))
        arena, receipt = build_instance(world, request)
        model = mujoco.MjModel.from_xml_path(arena)
        counts[object_type] = (model.nflex, model.nflexvert)
        assert receipt["configuration"] == {"snack": object_type}
        assert receipt["source_sha256"]
        fingerprints.add(receipt["arena_sha256"])
    assert counts == {"snack_rigid": (0, 0), "soft_snack": (1, 60), "cloth": (1, 42)}
    assert len(fingerprints) == 3
    if before is not None:
        assert shared.read_bytes() == before


def test_unknown_slots_and_types_fail_without_fallback():
    registry = Registry()
    for invalid in ({"snack": "unknown"}, {"weather": "rain"}):
        with pytest.raises(ValidationError):
            resolve_configuration(registry.world("so101-pick-place").configuration, invalid)
    with pytest.raises(ValidationError):
        resolve_configuration(registry.world("so101-hanoi-five").configuration, {"snack": "cloth"})
    values = resolve_configuration(registry.world("so101-sort-snacks").configuration, {"chips": "cloth"})
    assert values == {"chips": "cloth", "cookies": "cookie_box", "drink": "drink_can"}


def test_reset_is_revisioned_idempotent_and_instance_bound():
    sim = SimpleNamespace(epoch="current", scene_operator=None, reset=Mock(), instance_receipt={"configuration": {"snack": "cloth"}})
    controller = WorldController(sim)
    request = {"instance_id": "current", "revision": 0, "request_id": "once", "command": "reset", "arguments": {}}
    result = controller.invoke(request)
    assert controller.invoke(request) == result
    sim.reset.assert_called_once()
    assert result["revision"] == 1
    assert controller.describe()["state"]["configuration"]["snack"] == "cloth"
    for fields in ({"instance_id": "old", "request_id": "old"}, {"request_id": "stale"}, {"arguments": {"foo": 1}}):
        with pytest.raises(ValueError):
            controller.invoke({**request, **fields})
    sim.reset.assert_called_once()


def test_real_world_control_rejected_before_any_io():
    hub = SimpleNamespace(store=SimpleNamespace(get=lambda _: SimpleNamespace(mode="real", world=None)), world_client=Mock())
    with pytest.raises(ValueError, match="simulation"):
        WorldControls(hub).invoke("session", "reset", {})
    hub.world_client.assert_not_called()
