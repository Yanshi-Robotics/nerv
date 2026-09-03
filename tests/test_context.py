from nerv.brain import prompts
from nerv.platform.session import context


def test_orphan_tool_calls_get_honest_placeholder():
    msgs = [{"role": "user", "text": "go"},
            {"role": "assistant", "text": "", "tool_calls": [{"id": "c1", "name": "move_forward", "arguments": {}}]}]
    out = context.build(msgs)
    assert out[-1]["role"] == "tool" and out[-1]["content"] == prompts.ORPHAN_TOOL_RESULT


def test_leading_orphan_results_dropped_and_perception_not_resent():
    msgs = [{"role": "tool", "id": "x", "name": "n", "content": "stale"},
            {"role": "perception", "state": {}},
            {"role": "user", "text": "hi"}]
    out = context.build(msgs)
    assert [m["role"] for m in out] == ["user"]
