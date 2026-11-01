"""Project persistence and lifecycle checks; no device or model is used."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nerv.platform.projects import ProjectService, ProjectStore
from nerv.platform.session import SessionStore


def test_project_is_configuration_and_revisions_are_immutable(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create({"name": "Test", "mode": "sim"})
    updated = store.update(project["id"], {"expected_revision": 1, "name": "Updated"})
    assert updated["revision"] == 2
    assert '"name": "Test"' in (store.root / project["id"] / "revisions/1.json").read_text()
    assert not store.runs(project["id"])
    with pytest.raises(ValueError, match="reload"):
        store.update(project["id"], {"expected_revision": 1, "name": "Stale"})
    with pytest.raises(ValueError, match="separate project"):
        store.update(project["id"], {"expected_revision": 2, "mode": "real"})


@pytest.mark.parametrize("payload", [
    {"name": "Test", "mode": "sim2real"},
    {"name": "Test", "mode": "sim", "execution": "wam"},
    {"name": "Test", "mode": "sim", "path": "/elsewhere"},
    {"name": "Test", "mode": "sim", "view_layout": {"width": float("nan")}},
    {"name": " ", "mode": "real"},
])
def test_invalid_project_has_no_partial_record(tmp_path, payload):
    store = ProjectStore(tmp_path / "projects")
    with pytest.raises(ValueError):
        store.create(payload)
    assert store.list() == []


def test_migration_verified_backup_and_idempotent_history(tmp_path):
    sessions = SessionStore(str(tmp_path / "sessions"))
    robot, _ = sessions.new("operator", "fixture", "world")
    chat, _ = sessions.new("fixture-model", None, None)
    source = {str(p.relative_to(tmp_path / "sessions")): p.read_bytes()
              for p in (tmp_path / "sessions").rglob("*") if p.is_file()}
    store = ProjectStore(tmp_path / "projects")
    first = store.migrate(sessions)
    assert first == store.migrate(sessions)
    assert first["archived_sessions"] == [chat.id]
    assert len(store.list()) == 1
    run = store.runs(store.list()[0]["id"])[0]
    assert run["session_id"] == robot.id and run["status"] == "interrupted"
    for relative, content in source.items():
        assert (store.root / "migration-backup-v1" / relative).read_bytes() == content
        assert (tmp_path / "sessions" / relative).read_bytes() == content


def test_migration_resumes_after_partial_project_creation(tmp_path):
    sessions = SessionStore(str(tmp_path / "sessions"))
    session, _ = sessions.new("operator", "fixture", "world")
    store = ProjectStore(tmp_path / "projects")
    store.create({"name": "Recovered", "mode": "sim"}, project_id="p_legacy_" + session.id)
    store.migrate(sessions)
    assert len(store.list()) == 1
    assert len(store.runs(store.list()[0]["id"])) == 1


def test_host_restart_interrupts_metadata_without_device_calls(tmp_path):
    hub = Mock()
    store = ProjectStore(tmp_path / "projects")
    project = store.create({"name": "Test", "mode": "sim"})
    store.save_run(project["id"], {"id": "r_test", "status": "running", "created_at": 1})
    service = ProjectService(hub, store.root)
    assert service.active_run(project["id"]) is None
    assert service.get(project["id"])["runs"][0]["status"] == "interrupted"
    assert not hub.mock_calls


def test_real_start_requires_operator_before_any_side_effect(tmp_path):
    hub = Mock()
    service = ProjectService(hub, tmp_path / "projects")
    project = service.store.create({"name": "Real", "mode": "real", "robot": "fixture"})
    with pytest.raises(ValueError, match="person"):
        service.create_run(project["id"])
    assert service.store.runs(project["id"]) == []
    assert not hub.mock_calls


def test_start_pins_revision_and_repeated_start_reattaches(tmp_path):
    sessions = SessionStore(str(tmp_path / "sessions"))
    hub = Mock(store=sessions)
    hub.registry = SimpleNamespace(bodies={"fixture": object()}, worlds={})
    hub._control_error.return_value = None
    def create(*args, **kwargs):
        session, _ = sessions.new("operator", "fixture", None)
        session.mode = "real"
        sessions.save(session)
        return session.summary()
    hub.new_session.side_effect = create
    service = ProjectService(hub, tmp_path / "projects")
    project = service.store.create({"name": "Real", "mode": "real", "robot": "fixture"})
    started = service.create_run(project["id"], operator=True)
    service.store.update(project["id"], {"expected_revision": 1, "name": "Changed"})
    assert service.create_run(project["id"], operator=True)["run"]["id"] == started["run"]["id"]
    assert started["run"]["snapshot"]["name"] == "Real"
    assert hub.new_session.call_count == 1


def test_bad_ids_cannot_escape_store(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    for value in ("../escape", "/absolute", "a/b"):
        with pytest.raises(ValueError):
            store.get(value)


def test_world_mode_cannot_bypass_real_operator_boundary(tmp_path):
    hub = Mock()
    world = SimpleNamespace(kind="real")
    hub.registry = SimpleNamespace(bodies={"fixture": object()}, worlds={"physical": world}, world=lambda _: world)
    service = ProjectService(hub, tmp_path / "projects")
    project = service.store.create({"name": "Invalid", "mode": "sim", "robot": "fixture", "world": "physical"})
    with pytest.raises(ValueError, match="world_mode_mismatch"):
        service.create_run(project["id"])
    assert not hub.new_session.called
    assert service.store.runs(project["id"]) == []


def test_migration_retry_uses_immutable_backup_and_fixed_policy(tmp_path, monkeypatch):
    from nerv.platform.control_schemes import SchemeStore
    sessions = SessionStore(str(tmp_path / "sessions"))
    schemes = SchemeStore(tmp_path / "schemes")
    scheme = schemes.save({"name": "ACT", "robot": "fixture", "mode": "sim", "template": "fixed_policy", "actions": ["act"]})
    session, _ = sessions.new("operator", "fixture", "world")
    session.title = "Long title " * 30
    session.control_scheme = scheme.id
    sessions.save(session)
    store = ProjectStore(tmp_path / "projects")
    original = store.save_run
    monkeypatch.setattr(store, "save_run", Mock(side_effect=RuntimeError("power lost")))
    with pytest.raises(RuntimeError):
        store.migrate(sessions, schemes)
    session.title = "Changed after backup"
    sessions.save(session)
    monkeypatch.setattr(store, "save_run", original)
    store.migrate(sessions, schemes)
    imported = store.list()[0]
    assert imported["execution"] == "act" and imported["scheme_revision"] == 1
    assert len(imported["name"]) == 120
    assert sessions.get(session.id).title == "Changed after backup"


def test_project_fields_written_in_initial_session_metadata(tmp_path):
    store = SessionStore(str(tmp_path))
    session, _ = store.new("operator", "fixture", None, project_id="p_test", run_id="r_test", project_revision=3, mode="real")
    first = store.memory(session.id).events()[0]["data"]
    assert (first["project_id"], first["run_id"], first["project_revision"], first["mode"]) == ("p_test", "r_test", 3, "real")


def test_project_pins_scheme_revision_and_new_choice_resolves_its_own_version(tmp_path):
    from nerv.platform.control_schemes import SchemeStore
    hub = Mock(schemes=SchemeStore(tmp_path / "schemes"))
    first = hub.schemes.save({"name": "First", "robot": "fixture", "mode": "sim", "template": "external_vlm", "actions": ["move"]})
    first = hub.schemes.save(first.model_dump())
    service = ProjectService(hub, tmp_path / "projects")
    project = service.create({"name": "Pinned", "mode": "sim", "robot": "fixture", "scheme": first.id})
    assert project["scheme_revision"] == 2
    second = hub.schemes.save({"name": "Second", "robot": "fixture", "mode": "sim", "template": "external_vlm", "actions": ["move"]})
    updated = service.update(project["id"], {"expected_revision": 1, "scheme": second.id})
    assert updated["scheme_revision"] == 1
    hub.schemes.save(second.model_dump())
    assert service.store.get(project["id"])["scheme_revision"] == 1
