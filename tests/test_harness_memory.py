"""Durability, context protocol and cancellation invariants, without hardware or APIs."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from nerv.harness.context import ContextManager, ContextOverflow, groups
from nerv.harness.memory import SessionMemory
from nerv.platform.session import SessionStore


def test_session_history_is_durable_scoped_and_serialized(tmp_path):
    store = SessionStore(str(tmp_path))
    session, _ = store.new("mock", None, None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: store.append(session.id, {"role": "user", "text": f"entry {n}"}), range(20)))
    restored = SessionStore(str(tmp_path)).get(session.id)
    assert len(restored.messages) == 20
    assert len({m["text"] for m in restored.messages}) == 20
    other, _ = store.new("mock", None, None)
    assert store.memory(other.id).search("entry") == []
    with pytest.raises(ValueError):
        store.memory("../another")


def test_legacy_session_is_migrated_without_losing_notes(tmp_path):
    path = tmp_path / "s_old.json"
    path.write_text(json.dumps(dict(id="s_old", brain="mock", body=None, world=None,
                                   status="active", created_at="2026-10-19", title="old",
                                   notes=["constraint"], messages=[{"role": "user", "text": "original"}])))
    store = SessionStore(str(tmp_path))
    assert store.get("s_old").notes == ["constraint"]
    assert store.memory("s_old").search("original")[0]["data"]["text"] == "original"
    assert (tmp_path / "s_old.json.legacy").exists()


def test_compaction_keeps_original_and_can_retrieve_it(tmp_path):
    memory = SessionMemory(tmp_path, "s_test")
    for role, text in [("user", "prior constraint " * 1000), ("assistant", "historical result " * 1000),
                       ("user", "current request"), ("assistant", "current reply")]:
        memory.append("message", {"role": role, "text": text})
    manager = ContextManager(500, 40, 20)
    system, history = manager.prepare("instructions", memory.messages(), [], [], memory,
                                     lambda *_: ("Prior constraint and result.", "test-summarizer", {}))
    assert "Prior constraint" in system and len(history) == 2
    assert len(memory.messages()) == 4
    assert memory.search("historical result")
    assert memory.summary()["end"] == 2


def test_cancelled_compaction_does_not_commit(tmp_path):
    memory = SessionMemory(tmp_path, "s_test")
    for role in ["user", "assistant", "user", "assistant"]:
        memory.append("message", {"role": role, "text": "history " * 1000})
    source = memory.events()[:2]
    assert not memory.commit_summary(1, 2, "short", memory.fingerprint(source), "test", {}, lambda: True)
    assert memory.summary() is None


def test_context_rejects_malformed_tool_groups():
    with pytest.raises(ContextOverflow):
        groups([{"role": "assistant", "tool_calls": [{"id": "a"}]},
                {"role": "tool", "id": "other"}])


def test_image_and_tools_are_in_request_budget():
    manager = ContextManager(200, 50, 100)
    with pytest.raises(ContextOverflow):
        manager.prepare("system", [{"role": "user", "text": "hi"}], [], [{}, {}])


def test_journal_damage_is_not_silently_ignored(tmp_path):
    memory = SessionMemory(tmp_path, "s_test")
    memory.append("message", {"role": "user", "text": "hello"})
    with memory.path.open("ab") as file:
        file.write(b'{"seq":2')
    with pytest.raises(ValueError, match="incomplete"):
        memory.append("message", {})
