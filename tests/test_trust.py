"""The trust store is bound to what a node says, not to where it answers from."""
from nerv.nerve.body import KIND_PRIMITIVE, ToolSpec
from nerv.platform import trust


def _tools(desc="move"):
    return [ToolSpec("go", desc, {"type": "object", "properties": {}}, KIND_PRIMITIVE)]


def test_same_node_on_another_port_stays_trusted(tmp_path, monkeypatch):
    monkeypatch.delenv(trust.TRUST_ALL_ENV, raising=False)
    store = trust.TrustStore(str(tmp_path / "trust.json"))
    assert store.check("body:x", _tools(), "hi", url="http://127.0.0.1:8114").state == trust.UNKNOWN
    store.approve("body:x", _tools(), "hi", "x", url="http://127.0.0.1:8114")
    again = trust.TrustStore(str(tmp_path / "trust.json"))          # reloaded from disk
    d = again.check("body:x", _tools(), "hi", url="http://127.0.0.1:8117")   # the launcher moved it
    assert d.state == trust.TRUSTED, d
    d = again.check("body:x", _tools("MOVE FAST"), "hi", url="http://127.0.0.1:8117")
    assert d.state == trust.CHANGED and any("description changed" in c for c in d.changes)
    assert again.check("tool:x", _tools(), "hi").state == trust.UNKNOWN   # identity includes the kind


def test_url_keyed_v1_store_is_dropped_not_misread(tmp_path, monkeypatch):
    monkeypatch.delenv(trust.TRUST_ALL_ENV, raising=False)
    p = tmp_path / "trust.json"
    p.write_text('{"version": 1, "nodes": {"http://127.0.0.1:8114": {"hash": "x", "manifest": {}}}}')
    store = trust.TrustStore(str(p))
    assert store.list_approved() == {}
    assert store.check("body:x", _tools(), "hi").state == trust.UNKNOWN
