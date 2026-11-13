"""Cancellation must preserve manual control without clearing emergency holds."""
from types import SimpleNamespace

import pytest

from nerv.body.families.arm import ArmBody
from nerv.body.skills import StopFlag
from nerv.runtime.builtin import BuiltinRuntime


@pytest.mark.parametrize('held', [False, True])
def test_cancel_captures_targets_and_preserves_existing_emergency_latch(held):
    writes = []
    arm = ArmBody.__new__(ArmBody)
    arm.joints = ['shoulder', 'elbow']
    arm._held = held
    arm.runner = SimpleNamespace(stop=StopFlag())
    arm._read = lambda:(None, {'shoulder':.1, 'elbow':-.2})
    arm.bus = SimpleNamespace(write=writes.append)
    runtime = BuiltinRuntime(SimpleNamespace(id='fixture', family='arm'), None)
    runtime._impl, runtime.stop = arm, arm.runner.stop
    result = runtime.cancel_current('Manual control takeover')
    assert result['ok'] and result['dispatch_revoked'] and runtime.stop.is_set()
    assert writes[0].targets == [.1, -.2]
    assert arm.held == held
    assert not result['stop_confirmed'], 'A captured target is not measured physical stopping'
    assert arm.hold()['held'] and arm.held
