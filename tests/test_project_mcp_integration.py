"""Real Host services with an inert Runtime transport; no simulation or hardware evidence."""
import base64
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nerv import paths
from nerv.nerve.body import ActionResult, Capabilities, Observation, ToolSpec
from nerv.platform.agent_gateway import AgentGateway
from nerv.platform.hub import Nerv
from nerv.platform.projects import ProjectService
from nerv.platform.session import SessionStore


@pytest.fixture
def integrated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "NERV_HOME", str(tmp_path))
    monkeypatch.setattr("nerv.platform.module_jobs.jobs", lambda: [])
    body = SimpleNamespace(name="fixture", buses={"sim": SimpleNamespace(enabled=True)}, policy_error=None, skills={})
    assets=tmp_path / 'fixture-assets'
    assets.mkdir()
    (assets / 'fixture.xml').write_text('<mujoco/>')
    world = SimpleNamespace(name="fixture-world", kind="sim", supports={"fixture": "fixture.xml"}, configuration={}, assets_root=str(assets))
    registry = SimpleNamespace(bodies={body.name: body}, worlds={world.name: world}, tools={},
                               body=lambda _: body, world=lambda _: world)
    launcher = Mock(nodes={})
    hub = Nerv(registry=registry, launcher=launcher, store=SessionStore(str(tmp_path / "sessions")))
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lfoAAAAASUVORK5CYII=")
    execution = {"running": False}
    sequence = 0
    invoked = threading.Event()
    def observe():
        nonlocal sequence
        sequence += 1
        return Observation({"frame_seq": sequence}, [{"name": "camera", "png": image}])
    def invoke(name, *args, **kwargs):
        if name == "act":
            execution["running"] = True
        invoked.set()
        return ActionResult(True, "Fixture execution accepted")
    def stop(*args, **kwargs):
        execution["running"] = False
        return {"ok": True}
    robot = SimpleNamespace(name="fixture", capabilities=lambda: Capabilities("fixture", "3", robot_actions=[
        ToolSpec("move", "Bounded move", {"type": "object", "properties": {}, "additionalProperties": False}, "primitive"),
        ToolSpec("act", "Fixture ACT", {"type": "object", "properties": {}, "additionalProperties": False}, "skill")],
        sensors=["camera"], config={"external_supervision": {"validated": True, "timeout_s": 30, "max_observation_age_s": 2}}),
        health=lambda: {"epoch": "fixture-epoch", "supervision_sensors": ["camera"]}, perceive=observe,
        set_config=Mock(side_effect=lambda key, value: {"ok": True, key: value}),
        invoke=Mock(side_effect=invoke), post=Mock(side_effect=lambda *a, **k: {"ok": True, "execution_id": "fixture-execution", **execution}))
    # Runtime discovery/transport are the only substitutes. Project creation,
    # Session identity, scheme resolution, task dispatch, and gateway stay real.
    wc = SimpleNamespace(epoch=lambda: "fixture-epoch", sensors=lambda: [], health=lambda: {"configuration": {"configuration": {}}})
    monkeypatch.setattr(hub, "body_client", lambda _: robot)
    monkeypatch.setattr(hub, "world_client", lambda *_: wc)
    monkeypatch.setattr(hub, "_control_error", lambda _: None)
    monkeypatch.setattr(hub, "claim_robot", Mock(return_value="fixture-grant"))
    monkeypatch.setattr(hub, "stop_robot", Mock(side_effect=stop))
    monkeypatch.setattr(hub, "new_role_model", Mock(side_effect=AssertionError("No model calls permitted")))
    service = ProjectService(hub)
    scheme = hub.schemes.save({"name": "External", "robot": body.name, "mode": "sim", "template": "external_vlm", "actions": ["move"]})
    project = service.store.create({"name": "Integration", "mode": "sim", "robot": body.name, "world": world.name,
                                    "execution": "external_vlm", "scheme": scheme.id, "scheme_revision": scheme.revision})
    gateway = AgentGateway(hub, service)
    paired = gateway.create("Fixture Agent", [project["id"]], ["observe", "control"])
    yield SimpleNamespace(hub=hub, projects=service, project=project, gw=gateway, pair=paired,
                          robot=robot, launcher=launcher, execution=execution, invoked=invoked,
                          monkeypatch=monkeypatch)
    gateway.shutdown()
    for token in hub.tasks._tokens.values():
        token.set()
    for thread in hub.tasks._threads.values():
        thread.join(2)


def ready_binding(core, fixture, binding):
    """Explicit operator resource setup over inert physics/Runtime transports.

    A Run must not construct either transport. The real resource and World stores
    represent that separate setup before tests exercise execution authorization.
    """
    import httpx
    f = fixture
    pid = binding['project_id']
    project = f.projects.store.get(pid)
    world = f.projects.worlds.create(project['world'], project['robot'])
    resource = core.resources.for_project(pid)
    resource.update(shared_world=True,world_instance_id=world['world_instance_id'],
                    status='ready',runtime_epoch='fixture-epoch')
    core.resources.save(resource)
    run = f.projects.active_run(pid)
    run.update(shared_world=True,world_instance_id=world['world_instance_id'],world_generation=1)
    f.projects.store.save_run(pid,run)
    world.update(status='ready',epoch='fixture-epoch',roster=[dict(project_id=pid,
        robot_instance_id=resource['robot_instance_id'],run_id=run['id'],spawn=dict(x=0,y=0,z=1,yaw=0))])
    f.projects.worlds.save(world)
    f.launcher.nodes['shared_world:'+world['world_instance_id']] = SimpleNamespace(alive=lambda:True,url='http://fixture')
    f.launcher.nodes['body:'+resource['robot_instance_id']] = SimpleNamespace(meta={'epoch':'fixture-epoch'},alive=lambda:True)
    original_get = httpx.get
    f.monkeypatch.setattr(httpx,'get',lambda url,*args,**kwargs:
        SimpleNamespace(json=lambda:{'ok':True,'epoch':'fixture-epoch'}) if url=='http://fixture/health'
        else original_get(url,*args,**kwargs))
    return core.describe(run)


def call(fixture, operation, **arguments):
    return fixture.gw.invoke(fixture.pair["token"], operation, {"project_id": fixture.project["id"], **arguments})


def start_run(fixture):
    result = call(fixture, "run_start", request_id="create-run")
    session = fixture.projects.require_session(fixture.project["id"])
    assert not session.armed
    # Explicit operator authorization in the test. MCP never arms the robot.
    fixture.hub.store.set_armed(session.id, True)
    fixture.gw.set_control(fixture.project["id"], accepting=True, owner_id=fixture.pair["connection"]["id"])
    return result, session


def acknowledge(fixture):
    observation = call(fixture, "observe")
    assert call(fixture, "acknowledge", receipt_id=observation["receipt_id"])["ok"]
    return observation


def test_project_run_identity_scope_dispatch_and_takeover(integrated):
    f = integrated
    assert not f.hub.store.all() and not f.launcher.ensure_body.called
    outsider = f.projects.store.create({"name": "Outside", "mode": "sim"})
    with pytest.raises(PermissionError):
        f.gw.invoke(f.pair["token"], "project_get", {"project_id": outsider["id"]})
    result, session = start_run(f)
    assert call(f, "run_start", request_id="create-run") == result
    assert call(f, "run_start", request_id="reattach")["run"]["id"] == result["run"]["id"]
    f.launcher.ensure_body.assert_called_once()
    initial = f.hub.store.memory(session.id).events()[0]["data"]
    assert (initial["project_id"], initial["run_id"], initial["project_revision"]) == (f.project["id"], result["run"]["id"], 1)
    assert initial["mode"] == "sim" and session.brain == "operator"
    acknowledge(f)
    task = call(f, "task_start", request_id="task", goal="Move once")["data"]
    arguments = dict(request_id="move", task_id=task["task_id"], version=task["version"], action="move", arguments={})
    outcome = call(f, "action", **arguments)
    assert outcome["ok"] and call(f, "action", **arguments) == outcome
    f.robot.invoke.assert_called_once()
    pending = call(f, "observe")
    assert f.gw.set_control(f.project["id"], accepting=False)["ok"]
    assert f.hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"
    with pytest.raises(PermissionError):
        call(f, "action", **{**arguments, "request_id": "late"})
    with pytest.raises(PermissionError):
        call(f, "acknowledge", receipt_id=pending["receipt_id"])
    # A duplicate completed request reports its recorded outcome, never replays.
    assert call(f, "action", **arguments) == outcome
    f.robot.invoke.assert_called_once()
    f.hub.new_role_model.assert_not_called()


def test_selected_fixed_scheme_persists_without_mutating_run_snapshot(integrated):
    f = integrated
    result, session = start_run(f)
    fixed = f.hub.schemes.save({"name": "ACT", "robot": "fixture", "mode": "sim", "template": "fixed_policy", "actions": ["act"]})
    f.projects.select_scheme(session.id, fixed.id, fixed.revision)
    updated = f.projects.get(f.project["id"])
    assert updated["execution"] == "act" and updated["scheme"] == fixed.id and updated["scheme_revision"] == 1
    assert updated["revision"] == 2
    assert f.projects.active_run(f.project["id"])["snapshot"] == result["run"]["snapshot"]
    assert result["run"]["snapshot"]["execution"] == "external_vlm"
    assert f.hub.store.get(session.id).control_scheme == fixed.id
    acknowledge(f)
    task = call(f, "task_start", request_id="act-task", goal="Run fixture ACT")["data"]
    assert f.invoked.wait(2)
    assert task["supervisor"] == {"kind": "external_agent", "client_id": f.pair["connection"]["id"], "control_generation": f.gw.control(f.project["id"])["control_generation"]}
    with pytest.raises(ValueError, match="acknowledged Agent observations"):
        f.hub.tasks.allow_operator_supervision(session.id)
    assert call(f, "task_cancel", request_id="stop-act")["ok"]
    f.hub.new_role_model.assert_not_called()


def test_paused_commands_keep_supervision_and_explicit_stop_available(integrated):
    f = integrated
    _, session = start_run(f)
    acknowledge(f)
    task = call(f, "task_start", request_id="start", goal="Wait for action")["data"]
    f.gw.pause(f.project["id"])
    assert f.hub.tasks.snapshot(session.id)["task"]["status"] == "running"
    with pytest.raises(PermissionError):
        call(f, "action", request_id="paused-action", task_id=task["task_id"], version=1, action="move", arguments={})
    acknowledge(f)
    assert f.gw.control(f.project["id"])["supervision"] is not None
    f.robot.invoke.assert_not_called()
    assert call(f, "task_cancel", request_id="stop")["ok"]
    assert f.hub.tasks.snapshot(session.id)["task"]["status"] == "cancelled"


def test_observation_cannot_be_relabelled_after_control_or_world_changes(integrated, monkeypatch):
    f=integrated
    _,session=start_run(f)
    original=f.robot.perceive
    for change in ['control','world']:
        def delayed():
            observed=original()
            if change=='control':
                with f.gw._lock:
                    f.gw._invalidate_control(f.project['id'])
            else:
                latest=f.hub.store.get(session.id)
                latest.world_generation += 1
                f.hub.store.save(latest)
            return observed
        monkeypatch.setattr(f.robot,'perceive',delayed)
        with pytest.raises(ValueError,match='changed while collecting'):
            call(f,'observe')
        assert (f.pair['connection']['id'],f.project['id']) not in f.gw._receipts
