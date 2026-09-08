"""Velocity commands cannot bypass missing support or an operator's world lease."""
from types import SimpleNamespace

import pytest

from nerv.body.families.humanoid import HumanoidBody, DEFAULT_LOCOMOTION
from nerv.nerve.world import BusState

CLEAR = dict(valid=True, reason="clear", travel_clearance_m=1.0, turn_clear=True)


def body_with_scans(scans):
    body = HumanoidBody.__new__(HumanoidBody)
    body.loco = dict(DEFAULT_LOCOMOTION, poll_s=0, settle_s=0)
    body._clearance_query, body._clearance_period = {}, .1
    body.has_free_base = False
    commands, queries = [], []
    state = {"t": 0., "x": 0., "yaw": 0., "cmd": (0., 0., 0.)}
    def command(vx, vy, wz):
        state["cmd"] = vx, vy, wz
        commands.append((vx, vy, wz))
    def read():
        state["t"] += .05
        state["x"] += state["cmd"][0] * .05
        state["yaw"] += state["cmd"][2] * .05
        return BusState(state["t"], [], [], odom_xy=[state["x"], 0], odom_yaw=state["yaw"])
    def measure(query):
        queries.append(query)
        result = scans[min(len(queries)-1, len(scans)-1)]
        if isinstance(result, Exception):
            raise result
        return result
    body.bus = SimpleNamespace(read=read, clearance=measure)
    body._set_command = command
    return body, commands, queries


@pytest.mark.parametrize("scan", [{"valid": False, "reason": "scene_test_active"},
    {"valid": False, "reason": "unavailable"}, RuntimeError("sensor lost"),
    dict(CLEAR, travel_clearance_m=.1, reason="drop")])
def test_no_nonzero_command_before_safe_preflight(scan):
    body, commands, _ = body_with_scans([scan])
    result = body._drive_until(.6, 0, 0, 3, "dist", 1, None, lambda: False)
    assert all(command == (0, 0, 0) for command in commands)
    assert result["reason"] != "reached"
    assert result["moved_m"] == 0


@pytest.mark.parametrize("kind", ["dist", "yaw"])
def test_active_lease_interrupts_motion_and_leaves_standing_command(kind):
    body, commands, queries = body_with_scans([CLEAR, {"valid": False, "reason": "scene_test_active"}])
    velocity = (.6, 0, 0) if kind == "dist" else (0, 0, .8)
    result = body._drive_until(*velocity, 3, kind, 1, None, lambda: False)
    assert velocity in commands
    assert commands[-1] == (0, 0, 0)
    assert result["reason"] == "scene_test_active"
    assert result["moved_m"] < .2
    assert len(queries) == 2


def test_turn_envelope_preflight_blocks_rotation():
    body, commands, queries = body_with_scans([dict(CLEAR, turn_clear=False, reason="obstacle")])
    result = body._drive_until(0, 0, .8, 3, "yaw", 1, None, lambda: False)
    assert result["braked"] and result["turned_deg"] == 0
    assert queries[0]["motion"] == "turn"
    assert all(command == (0, 0, 0) for command in commands)


def test_flat_motion_uses_measured_progress_and_rechecks():
    body, commands, queries = body_with_scans([CLEAR])
    result = body._drive_until(.6, 0, 0, 3, "dist", .6, None, lambda: False)
    assert result["reason"] == "reached" and .6 <= result["moved_m"] < .7
    assert len(queries) > 5 and commands[-1] == (0, 0, 0)
