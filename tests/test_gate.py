from nerv.nerve.body import KIND_PRIMITIVE, KIND_READ, KIND_SKILL, ToolSpec
from nerv.platform.gate import ALLOW, DENY, SafetyGate

SPEC = ToolSpec("move_forward", "walk", {"type": "object", "properties": {
    "meters": {"type": "number", "minimum": 0, "maximum": 3}}}, KIND_SKILL)


def test_disarmed_refuses_mutating_but_not_read():
    g = SafetyGate()
    d, reason = g.decide("move_forward", {"meters": 1}, kind=KIND_SKILL, origin="body", armed=False, spec=SPEC)
    assert d == DENY and "not armed" in reason
    d, _ = g.decide("read_joints", {}, kind=KIND_READ, origin="body", armed=False)
    assert d == ALLOW


def test_armed_allows_and_ranges_bind():
    g = SafetyGate()
    assert g.decide("move_forward", {"meters": 1}, kind=KIND_SKILL, origin="body", armed=True, spec=SPEC)[0] == ALLOW
    d, reason = g.decide("move_forward", {"meters": 50}, kind=KIND_SKILL, origin="body", armed=True, spec=SPEC)
    assert d == DENY and "maximum" in reason


def test_tools_are_not_subject_to_arming_but_to_rules():
    g = SafetyGate(blocked=("calc",))
    assert g.decide("calc", {"expression": "1"}, kind=KIND_READ, origin="tool", armed=False)[0] == DENY
    g2 = SafetyGate()
    assert g2.decide("calc", {"expression": "1"}, kind=KIND_PRIMITIVE, origin="tool", armed=False)[0] == ALLOW
