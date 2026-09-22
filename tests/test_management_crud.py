"""
The CRUD factory (tools/_crud.py) and the shared json envelope (tools/_json.py).

Six modules — flow, loop, team, scenario, world and project management — used
to hand-write their own ``@tool`` wiring for list/create/get/modify/delete/
validate: a decorator, an args_schema, a try/except, a json_ok/json_err
envelope. This file is the regression net for pulling that shared shape into
``build_entity_tools``. It checks the JSON *keys* each operation answers with
are exactly what they were before the refactor (captured by exercising the
pre-refactor tools and reading the current implementations); the business
logic itself — validation rules, merge semantics, conflict checks — is already
covered by test_entity_tools.py, test_loops.py, test_teams.py and
test_worlds.py, so it is not duplicated here.
"""
import json

import pytest

from tools._json import json_err, json_ok


def call(tool, **kwargs):
    """Invoke a tool and parse its JSON answer."""
    return json.loads(tool.invoke(kwargs))


# ── tools/_json.py ───────────────────────────────────────────────────────────

def test_json_ok_wraps_the_payload_with_ok_true():
    assert json.loads(json_ok({"a": 1, "b": [1, 2]})) == {"ok": True, "a": 1, "b": [1, 2]}


def test_json_ok_with_an_empty_payload_is_just_ok():
    assert json.loads(json_ok({})) == {"ok": True}


def test_json_err_has_the_standard_fields_and_a_default_code():
    assert json.loads(json_err("bad thing")) == {
        "ok": False, "error": "bad thing", "code": "bad_request",
    }


def test_json_err_takes_an_explicit_code():
    out = json.loads(json_err("nope", code="not_found"))
    assert out["code"] == "not_found"


def test_json_err_merges_extra_on_top():
    out = json.loads(json_err("nope", code="not_found", extra={"flow_id": "f1", "count": 2}))
    assert out == {
        "ok": False, "error": "nope", "code": "not_found",
        "flow_id": "f1", "count": 2,
    }


# ── shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def cast(monkeypatch):
    """Two registered agents to build entities out of, and no others."""
    from agents.registry import AgentSpec
    specs = [
        AgentSpec(id="alpha", name="Alpha", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor",
                  description="does the first half"),
        AgentSpec(id="beta", name="Beta", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor",
                  description="does the second half"),
    ]
    import agents.registry as registry
    monkeypatch.setattr(registry, "list_agents", lambda: list(specs))
    monkeypatch.setattr(registry, "get_agent",
                        lambda aid: next((s for s in specs if s.id == aid), None))
    return specs


@pytest.fixture
def no_workspace(monkeypatch):
    """Run outside any workspace, so nothing is filtered by membership."""
    import common.workspace_context as wc
    monkeypatch.setattr(wc, "resolve_active_workspace", lambda preferred=None: None)
    for module in ("tools.flow_management", "tools.scenario_management",
                   "tools.team_management", "tools.loop_management",
                   "tools.world_management"):
        mod = __import__(module, fromlist=["resolve_active_workspace"])
        monkeypatch.setattr(mod, "resolve_active_workspace", lambda preferred=None: None)


# ── Flow: create, get, modify, validate, delete (no list — owned elsewhere) ──

def test_flow_tools_answer_with_the_same_shape_as_before(cast, no_workspace):
    from tools.flow_management import (
        create_flow_tool, delete_flow_tool, get_flow_tool, modify_flow_tool,
        validate_flow_tool,
    )

    created = call(create_flow_tool, name="Pipeline", nodes=[{"id": "n1", "agent_id": "alpha"}])
    assert set(created) == {"ok", "message", "flow_id", "flow"}
    fid = created["flow_id"]

    got = call(get_flow_tool, flow_id=fid)
    assert set(got) == {"ok", "flow", "entry_point"}

    modified = call(modify_flow_tool, flow_id=fid, description="updated")
    assert set(modified) == {"ok", "changed", "flow", "message"}

    valid = call(validate_flow_tool, flow_id=fid)
    assert set(valid) == {"ok", "valid", "errors", "warnings"}

    deleted = call(delete_flow_tool, flow_id=fid)
    assert set(deleted) == {"ok", "message", "flow_id"}

    missing = call(get_flow_tool, flow_id=fid)
    assert missing == {"ok": False, "error": "Flow not found", "code": "not_found", "flow_id": fid}


# ── Loop: list, create, get, modify, validate, delete ────────────────────────

@pytest.fixture
def one_flow(monkeypatch):
    flow = {"id": "flow-1", "name": "Write and review", "workspace": None,
            "nodes": [{"id": "n1", "data": {"agent_id": "alpha"}}], "edges": []}
    import flow.store as flow_store
    monkeypatch.setattr(flow_store, "get_flow",
                        lambda fid: flow if fid == flow["id"] else None)
    return flow


def test_loop_tools_answer_with_the_same_shape_as_before(cast, one_flow, no_workspace):
    from tools.loop_management import (
        create_loop_tool, delete_loop_tool, get_loop_tool, list_loops_tool,
        modify_loop_tool, validate_loop_tool,
    )

    listed = call(list_loops_tool)
    assert set(listed) == {"ok", "workspace", "count", "loops"}

    created = call(create_loop_tool, name="Polish", flow_id="flow-1",
                   exit_criterion="good enough")
    assert set(created) >= {"ok", "message", "loop_id", "loop"}
    lid = created["loop_id"]

    got = call(get_loop_tool, loop_id=lid)
    assert set(got) == {"ok", "loop"}

    modified = call(modify_loop_tool, loop_id=lid, description="x")
    assert set(modified) >= {"ok", "message", "loop_id", "loop"}

    valid = call(validate_loop_tool, loop_id=lid)
    assert set(valid) == {"ok", "valid", "errors", "warnings"}

    deleted = call(delete_loop_tool, loop_id=lid)
    assert set(deleted) == {"ok", "message", "loop_id"}

    missing = call(get_loop_tool, loop_id=lid)
    assert missing == {"ok": False, "error": "Loop not found", "code": "not_found", "loop_id": lid}


# ── Team: list, create, get, modify, delete (no validate tool) ───────────────

def test_team_tools_answer_with_the_same_shape_as_before(cast, no_workspace):
    from tools.team_management import (
        create_team_tool, delete_team_tool, get_team_tool, list_teams_tool,
        modify_team_tool,
    )

    listed = call(list_teams_tool)
    assert set(listed) == {"ok", "workspace", "count", "teams"}

    members = [{"agent_id": "alpha", "name": "Lead", "role": "lead", "manifest": "leads"}]
    created = call(create_team_tool, name="Platform", members=members,
                   leader_agent_id="alpha", charter="ship it")
    assert set(created) == {"ok", "message", "team_id", "team"}
    tid = created["team_id"]

    got = call(get_team_tool, team_id=tid)
    assert set(got) == {"ok", "team"}

    modified = call(modify_team_tool, team_id=tid, description="x")
    assert set(modified) == {"ok", "message", "team_id", "team"}

    deleted = call(delete_team_tool, team_id=tid)
    assert set(deleted) == {"ok", "message", "team_id"}

    missing = call(get_team_tool, team_id=tid)
    assert missing == {"ok": False, "error": "Team not found", "code": "not_found", "team_id": tid}


# ── Scenario: list, create, get, modify, validate, delete ────────────────────

def test_scenario_tools_answer_with_the_same_shape_as_before(cast, no_workspace):
    from tools.scenario_management import (
        create_scenario_tool, delete_scenario_tool, get_scenario_tool,
        list_scenarios_tool, modify_scenario_tool, validate_scenario_tool,
    )

    listed = call(list_scenarios_tool)
    assert set(listed) == {"ok", "workspace", "count", "scenarios"}

    roles = [{"agent_id": "alpha", "name": "Mara", "role": "innkeeper"}]
    created = call(create_scenario_tool, name="Tavern", environment="social", roles=roles)
    assert set(created) == {"ok", "message", "scenario_id", "scenario"}
    sid = created["scenario_id"]

    got = call(get_scenario_tool, scenario_id=sid)
    assert set(got) == {"ok", "scenario"}

    modified = call(modify_scenario_tool, scenario_id=sid, description="x")
    assert set(modified) == {"ok", "message", "scenario_id", "scenario"}

    valid = call(validate_scenario_tool, scenario_id=sid)
    assert set(valid) == {"ok", "valid", "errors", "warnings"}

    deleted = call(delete_scenario_tool, scenario_id=sid)
    assert set(deleted) == {"ok", "message", "scenario_id"}

    missing = call(get_scenario_tool, scenario_id=sid)
    assert missing == {"ok": False, "error": "Scenario not found", "code": "not_found",
                        "scenario_id": sid}


# ── World: list, create, get, modify, validate, delete ───────────────────────

def test_world_tools_answer_with_the_same_shape_as_before(no_workspace):
    from tools.world_management import (
        create_world_tool, delete_world_tool, get_world_tool, list_worlds_tool,
        modify_world_tool, validate_world_tool,
    )

    listed = call(list_worlds_tool)
    assert set(listed) == {"ok", "workspace", "count", "worlds"}

    created = call(create_world_tool, name="Keep", locations=[{"name": "yard"}])
    assert set(created) == {
        "ok", "world_id", "env_id", "name", "workspace", "world",
        "problems", "notes", "created",
    }
    wid = created["world_id"]

    got = call(get_world_tool, world_id=wid)
    assert set(got) == {
        "ok", "world_id", "env_id", "name", "workspace", "world", "problems", "notes",
    }

    modified = call(modify_world_tool, world_id=wid, description="x")
    assert set(modified) == {
        "ok", "world_id", "env_id", "name", "workspace", "world",
        "problems", "notes", "updated",
    }

    valid = call(validate_world_tool, world_id=wid)
    assert set(valid) == {"ok", "world_id", "valid", "problems", "notes"}

    deleted = call(delete_world_tool, world_id=wid)
    assert set(deleted) == {"ok", "world_id", "deleted"}

    missing = call(get_world_tool, world_id=wid)
    assert missing == {"ok": False, "error": f"World not found: {wid}", "code": "not_found"}


# ── Project: list, create, get, modify, delete (no validate tool) ────────────

@pytest.fixture
def workspace(monkeypatch, tmp_path):
    """A workspace that exists on disk, so a project can own a folder in it."""
    import tools.project_management as pm
    from projects.storage import ProjectStore

    folder = tmp_path / "ws"
    folder.mkdir()
    store = ProjectStore(path=tmp_path / "projects.json")
    monkeypatch.setattr(pm, "_store", lambda: store)
    monkeypatch.setattr(pm, "resolve_active_workspace", lambda preferred=None: "ws")

    import workspace as ws_module
    monkeypatch.setattr(ws_module, "get_workspace_folder",
                        lambda name: folder if name == "ws" else None)
    monkeypatch.setattr(ws_module, "resolve_project_root",
                        lambda ws, sub: (folder / sub).resolve())
    return folder


def test_project_tools_answer_with_the_same_shape_as_before(workspace):
    from tools.project_management import (
        create_project_tool, delete_project_tool, get_project_tool,
        list_projects_tool, modify_project_tool,
    )

    listed = call(list_projects_tool)
    assert set(listed) == {"ok", "workspace", "count", "projects"}

    created = call(create_project_tool, name="Pricing Study", type="research")
    assert set(created) == {"ok", "message", "project_id", "project"}
    pid = created["project_id"]

    got = call(get_project_tool, project_id=pid)
    assert set(got) == {"ok", "project"}

    modified = call(modify_project_tool, project_id=pid, status="archived")
    assert set(modified) == {"ok", "message", "project_id", "project"}

    deleted = call(delete_project_tool, project_id=pid)
    assert set(deleted) == {"ok", "message", "project_id"}

    missing = call(get_project_tool, project_id=pid)
    assert missing == {"ok": False, "error": "Project not found", "code": "not_found",
                        "project_id": pid}


# ── The factory itself ────────────────────────────────────────────────────────

def test_build_entity_tools_emits_tools_in_a_fixed_order():
    """list/create/get/modify/delete/validate, skipping the slots an entity
    does not use — so a module's aggregate constant lines up with the order
    it always documented."""
    from pydantic import BaseModel
    from tools._crud import EntityToolSpec, ToolDef, build_entity_tools

    class In(BaseModel):
        pass

    def _get():
        """Get a thing."""
        return "{}"

    def _list():
        """List things."""
        return "{}"

    def _delete():
        """Delete a thing."""
        return "{}"

    spec = EntityToolSpec(
        singular="thing", plural="things",
        get=ToolDef("get_thing_tool", In, _get, "Failed to get thing"),
        list=ToolDef("list_things_tool", In, _list, "Failed to list things"),
        delete=ToolDef("delete_thing_tool", In, _delete, "Failed to delete thing"),
    )
    tools = build_entity_tools(spec)
    assert [t.name for t in tools] == ["list_things_tool", "get_thing_tool", "delete_thing_tool"]


def test_an_unhandled_exception_becomes_a_json_err_with_the_error_prefix():
    """The factory's own fallback — the outer try/except every hand-written
    tool used to have — for whatever a handler did not itself turn into a
    json_err."""
    from pydantic import BaseModel
    from tools._crud import EntityToolSpec, ToolDef, build_entity_tools

    class In(BaseModel):
        pass

    def _boom():
        """Get a thing."""
        raise RuntimeError("disk is on fire")

    spec = EntityToolSpec(
        singular="thing", plural="things",
        get=ToolDef("get_thing_tool", In, _boom, "Failed to get thing"),
    )
    tool, = build_entity_tools(spec)
    out = json.loads(tool.invoke({}))
    assert out == {"ok": False, "error": "Failed to get thing: disk is on fire",
                   "code": "bad_request"}


def test_a_handlers_docstring_becomes_the_tools_description():
    """The catalog (tools/registry.py) reads a tool's description from here."""
    from pydantic import BaseModel
    from tools._crud import EntityToolSpec, ToolDef, build_entity_tools

    class In(BaseModel):
        pass

    def _handler():
        """One line a catalog entry should show."""
        return "{}"

    spec = EntityToolSpec(
        singular="thing", plural="things",
        get=ToolDef("get_thing_tool", In, _handler, "Failed to get thing"),
    )
    tool, = build_entity_tools(spec)
    assert tool.description == "One line a catalog entry should show."
