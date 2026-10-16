"""
run_new_flow_tool (tools/flow_management.py): an agent builds a flow for one
request and runs it in one call, as a temporary flow no list shows.

The launch itself is replaced by a recorder: what is under test is what gets
checked, stored and started, not the flow subprocess.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agents import registry
from common import auto_mode
from flow import store as flow_store
from tools import flow_management as fm


@pytest.fixture
def agents(monkeypatch):
    for aid in ("finder", "checker", "writer"):
        registry.add_agent(registry.AgentSpec(
            id=aid, name=aid.title(), type="standard",
            entrypoint="agents.standard_agent:StandardAgent"))
    monkeypatch.setattr(fm, "resolve_active_workspace", lambda preferred=None: None)
    return ["finder", "checker", "writer"]


@pytest.fixture
def launches(monkeypatch):
    calls: list = []

    def _launch(flow_id, flow, ws, desc, *, create_task=False):
        calls.append({"flow_id": flow_id, "desc": desc, "create_task": create_task})
        return {"message": "Flow started.", "flow_id": flow_id, "run_id": "fr-1",
                "session_id": "s-1", "workspace": ws, "taskless": not create_task}

    import tools.langchain_tools as lt
    monkeypatch.setattr(lt, "launch_flow_run", _launch)
    return calls


NODES = [
    {"id": "find", "agent_id": "finder", "task": "List the claims as a JSON array."},
    {"id": "check", "entity_id": "map", "label": "Check each claim", "output": ["checked"],
     "config": {"agent_id": "checker", "task": "Check this claim."}},
    {"id": "write", "agent_id": "writer", "input": ["checked"], "task": "Write the report."},
]
EDGES = [{"source": "find", "target": "check"}, {"source": "check", "target": "write"}]


def _call(**kw):
    args = {"name": "Fact check", "goal": "Check the article", "nodes": NODES, "edges": EDGES}
    args.update(kw)
    return json.loads(fm.run_new_flow_tool.invoke(args))


def test_the_first_call_returns_the_plan_and_starts_nothing(agents, launches):
    out = _call()
    assert out["ok"] is False and out["code"] == "approval_required"
    steps = {n["id"]: n for n in out["plan"]["nodes"]}
    assert steps["find"]["task"] == "List the claims as a JSON array."
    assert steps["check"]["config"]["agent_id"] == "checker"
    assert "per item" in out["estimate"]["note"]
    assert launches == [] and flow_store.count_flows(include_ephemeral=True) == 0


def test_approved_it_stores_a_temporary_flow_and_starts_it(agents, launches):
    out = _call(user_approved=True)
    assert out["ok"], out
    flow_id = out["flow_id"]
    assert out["link"] == f"/flows/{flow_id}?run=fr-1" and out["ephemeral"] is True
    assert launches == [{"flow_id": flow_id, "desc": "Check the article", "create_task": False}]
    stored = flow_store.get_flow(flow_id)
    assert stored["ephemeral"] is True
    by_id = {n["id"]: n for n in stored["nodes"]}
    assert by_id["find"]["data"]["nodeTask"] == "List the claims as a JSON array."
    # Laid out by depth: each step one column further right.
    assert by_id["find"]["position"]["x"] < by_id["check"]["position"]["x"] < by_id["write"]["position"]["x"]
    # No list shows it; the run (by id) still finds it.
    assert flow_id not in [f["id"] for f in flow_store.list_flows()]
    assert flow_store.count_flows() == 0

    kept = json.loads(fm.modify_flow_tool.invoke({"flow_id": flow_id, "keep": True}))
    assert kept["ok"] and "kept" in kept["changed"]
    assert flow_id in [f["id"] for f in flow_store.list_flows()]


def test_save_keeps_it_from_the_start(agents, launches):
    out = _call(user_approved=True, save=True)
    assert out["ephemeral"] is False
    assert out["flow_id"] in [f["id"] for f in flow_store.list_flows()]


def test_auto_mode_starts_it_without_the_flag(agents, launches):
    token = auto_mode.set_turn(True)
    try:
        out = _call()
    finally:
        auto_mode.reset_turn(token)
    assert out["ok"], out
    assert len(launches) == 1


def test_an_invalid_graph_is_refused_before_anything(agents, launches):
    out = _call(nodes=NODES[:2] + [{"id": "write", "agent_id": "nobody"}], user_approved=True)
    assert out["code"] == "invalid_flow" and any("nobody" in e for e in out["errors"])
    routed = [{"id": "a", "agent_id": "finder", "route": "one", "route_default": "zzz"},
              {"id": "b", "agent_id": "writer"}, {"id": "c", "agent_id": "checker"}]
    out = _call(nodes=routed, edges=[{"source": "a", "target": "b"},
                                     {"source": "a", "target": "c"}], user_approved=True)
    assert any("route_default 'zzz'" in e for e in out["errors"])
    assert launches == [] and flow_store.count_flows(include_ephemeral=True) == 0


def test_the_callers_delegation_allowlist_holds(agents, launches, monkeypatch):
    import tools.langchain_tools as lt
    monkeypatch.setattr(lt, "_caller_delegates", lambda: {"finder", "writer"})
    out = _call(user_approved=True)
    assert out["code"] == "invalid_flow"
    assert any("checker" in e for e in out["errors"])  # the map step's agent counts too
    nested = NODES[:1] + [{"id": "sub", "entity_id": "run_team", "config": {"team_id": "t"}}]
    out = _call(nodes=nested, edges=[{"source": "find", "target": "sub"}], user_approved=True)
    assert any("starts another run" in e for e in out["errors"])
    assert launches == []


def test_old_temporary_flows_are_swept_when_a_new_one_is_saved(agents):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    flow_store.save_flow({"id": "old-tmp", "name": "old", "nodes": [], "edges": [],
                          "ephemeral": True, "updated_at": old})
    flow_store.save_flow({"id": "old-kept", "name": "kept", "nodes": [], "edges": [],
                          "updated_at": old})
    flow_store.save_flow({"id": "new-tmp", "name": "new", "nodes": [], "edges": [],
                          "ephemeral": True, "updated_at": datetime.now(timezone.utc).isoformat()})
    ids = {f["id"] for f in flow_store.list_flows(include_ephemeral=True)}
    assert "old-tmp" not in ids and {"old-kept", "new-tmp"} <= ids
