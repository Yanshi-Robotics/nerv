from nerv.nerve.capabilities import Capability, CapabilityRegistry
from nerv.harness.memory import SessionMemory
from nerv.harness.scoped_memory import ScopedMemory
import pytest


def test_domains_do_not_shadow_and_cannot_be_forged():
    registry = CapabilityRegistry()
    a = registry.register(Capability("agent_tools", "reset", "", {}))
    b = registry.register(Capability("world_control", "reset", "", {}))
    assert a != b
    assert registry.resolve(a).domain == "agent_tools"
    assert registry.resolve(b).domain == "world_control"
    with pytest.raises(ValueError):
        registry.resolve("world_control__reset")
    with pytest.raises(ValueError):
        registry.register(Capability("agent_tools", "reset", "", {}))


def test_scoped_history_and_compaction_do_not_leak(tmp_path):
    journal = SessionMemory(tmp_path, "session")
    journal.append("message", {"role": "user", "text": "private agent message"})
    task = ScopedMemory(journal, "task1")
    other = ScopedMemory(journal, "task2")
    task.append("message", {"role": "user", "text": "task observation " * 30})
    other.append("message", {"role": "user", "text": "different task"})
    source = task.events()
    assert len(task.messages()) == 1
    assert not task.search("different")
    assert not task.search("private")
    assert task.commit_summary(source[0]["seq"], source[-1]["seq"], "summary",
                               task.fingerprint(source), "fixture", None)
    assert other.summary() is None
    assert journal.summary() is None
    assert len(task.messages()) == 1
    assert not task.commit_summary(1, 99, "changed", "invalid", "fixture", None)


def test_journal_reader_waits_for_complete_write(tmp_path, monkeypatch):
    import threading
    journal = SessionMemory(tmp_path, "concurrent")
    entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
    original = journal._append
    def slow(events, kind, data):
        entered.set()
        assert release.wait(2)
        return original(events, kind, data)
    monkeypatch.setattr(journal, "_append", slow)
    writer = threading.Thread(target=journal.append, args=("message", {"text": "complete"}))
    received = []
    def read():
        received.extend(SessionMemory(tmp_path, "concurrent").events())
        read_done.set()
    writer.start()
    assert entered.wait(1)
    reader = threading.Thread(target=read)
    reader.start()
    assert not read_done.wait(0.05)
    release.set()
    writer.join(1)
    reader.join(1)
    assert received[0]["data"]["text"] == "complete"
