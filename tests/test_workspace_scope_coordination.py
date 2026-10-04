"""An agent's coordination tools reach only its own workspace
(common/workspace_scope.py).

Two workspaces, ``ws_a`` and ``ws_b``, each with its own records: tasks,
scheduled jobs, flows, worlds, scenarios, teams, loops, runs, eval sets, and
an agent of its own. A run in ``ws_a`` sees and changes ``ws_a``'s records;
``ws_b``'s answer like missing ones (or are refused, for a side effect). The
service's own agents (``SERVICE_WIDE_AGENTS``) still reach both.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest


def call(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


@contextmanager
def run_in(workspace, agent_id="main-agent"):
    """A tool call made by ``agent_id`` in a run of ``workspace``."""
    from agents.agent_loop import LoopState, reset_state, set_state
    from common.agent_context import current_agent_id
    from common.workspace_context import _workspace_ctx

    ws_token = _workspace_ctx.set(workspace)
    state_token = set_state(LoopState(run_id=f"run-{agent_id}", agent_id=agent_id, workspace=workspace))
    agent_token = current_agent_id.set(agent_id)
    try:
        yield
    finally:
        current_agent_id.reset(agent_token)
        reset_state(state_token)
        _workspace_ctx.reset(ws_token)


def service():
    return run_in("ws_a", agent_id="service_agent")


@pytest.fixture
def two_ws(monkeypatch):
    """``ws_a`` with ``agent_a``, ``ws_b`` with ``agent_b``; ``helper`` is in
    both, ``sysbot`` is a system agent."""
    import agents.registry as registry
    import tools.langchain_tools as lt
    from agents.registry import AgentSpec, add_agent
    from workspace import create_workspace_folder, update_workspace_metadata

    # tools.langchain_tools binds the registry functions at import; a module
    # first imported under another test's patch would keep that patch.
    for alias, real in (("reg_get_agent", "get_agent"), ("reg_list_agents", "list_agents"),
                        ("reg_add_agent", "add_agent"), ("reg_remove_agent", "remove_agent")):
        monkeypatch.setattr(lt, alias, getattr(registry, real))

    def _agent(agent_id, **extra):
        add_agent(AgentSpec(id=agent_id, name=agent_id.title(), type="langchain",
                            entrypoint="agents.agent_factory:build_agent_executor",
                            description=f"{agent_id} agent", tools=["read_file"], **extra))

    _agent("agent_a", owner_workspace="ws_a")
    _agent("agent_b", owner_workspace="ws_b")
    _agent("helper")
    _agent("sysbot", system=True)
    for ws, own in (("ws_a", "agent_a"), ("ws_b", "agent_b")):
        create_workspace_folder(ws)
        update_workspace_metadata(ws, {"allowed_agents": [own, "helper", "sysbot"]})
    yield
    from common.workspace_context import _workspace_ctx
    _workspace_ctx.set(None)


# ── tools/task_management.py ─────────────────────────────────────────────────

@pytest.fixture
def tasks(two_ws):
    from tasks.service import create_task
    return {
        "a": create_task(title="Task A", workspace="ws_a"),
        "b": create_task(title="Task B", workspace="ws_b"),
    }


def test_task_tools_see_only_this_workspace(tasks):
    from tools.task_management import (
        add_subtask, create_task, get_task, get_task_result, list_tasks, set_task_dependencies,
        stop_task, update_task,
    )
    a, b = str(tasks["a"].id), str(tasks["b"].id)
    with run_in("ws_a"):
        assert call(get_task, id=a)["ok"] is True
        for out in (call(get_task, id=b), call(update_task, id=b, title="x"), call(stop_task, id=b)):
            assert out["ok"] is False and out["code"] == "not_found"
        assert call(get_task_result, task_id=b)["code"] == "not_found"
        assert call(add_subtask, parent_id=b, title="child")["code"] == "not_found"
        assert call(create_task, title="dep", depends=[b])["code"] == "not_found"
        assert call(set_task_dependencies, id=a, depends=[b])["code"] == "not_found"
        assert call(update_task, id=a, parent_id=b)["code"] == "not_found"
        listed = call(list_tasks)
        assert [t["id"] for t in listed["tasks"]] == [a]
        assert call(add_subtask, parent_id=a, title="child")["ok"] is True
    from tasks.service import get_task as svc_get_task
    assert svc_get_task(tasks["b"].id).status.value != "stopped"


def test_a_service_wide_agent_reaches_another_workspaces_task(tasks):
    from tools.task_management import get_task, update_task
    b = str(tasks["b"].id)
    with service():
        assert call(get_task, id=b)["task"]["id"] == b
        assert call(update_task, id=b, description="seen by the service")["ok"] is True


def test_no_workspace_at_all_keeps_todays_behaviour(tasks, monkeypatch):
    import common.workspace_context as wc
    from tools import task_management
    monkeypatch.setattr(wc, "resolve_active_workspace", lambda preferred=None: None)
    monkeypatch.setattr(task_management, "resolve_active_workspace", lambda preferred=None: None)
    assert call(task_management.get_task, id=str(tasks["b"].id))["ok"] is True


# ── tools/schedule_management.py ─────────────────────────────────────────────

def test_scheduled_jobs_of_another_workspace_are_invisible(two_ws):
    from plans import service as plan_service
    from plans.models import JobKind
    from tools.schedule_management import (
        cancel_scheduled, list_scheduled, schedule_task, update_scheduled,
    )
    when = datetime.now(timezone.utc) + timedelta(days=1)
    mine = plan_service.create_job(kind=JobKind.notification, title="mine", run_at=when, workspace="ws_a")
    theirs = plan_service.create_job(kind=JobKind.notification, title="theirs", run_at=when, workspace="ws_b")
    with run_in("ws_a"):
        assert [j["title"] for j in call(list_scheduled)["jobs"]] == ["mine"]
        assert call(cancel_scheduled, id=str(theirs.id))["code"] == "not_found"
        assert call(update_scheduled, id=str(theirs.id), title="x")["code"] == "not_found"
        # A job that would run another workspace's agent here is refused.
        assert call(schedule_task, title="t", delay_minutes=5, agent_id="agent_b")["code"] == "not_found"
        assert call(update_scheduled, id=str(mine.id), agent_id="agent_b")["code"] == "not_found"
        assert call(schedule_task, title="t", delay_minutes=5, agent_id="agent_a")["ok"] is True
        assert call(cancel_scheduled, id=str(mine.id))["ok"] is True
    assert plan_service.get_job(theirs.id).status.value == "scheduled"
    with service():
        assert call(cancel_scheduled, id=str(theirs.id))["ok"] is True


# ── tools/flow_management.py and the flow tools of tools/langchain_tools.py ──

@pytest.fixture
def flows(two_ws):
    from flow import store as flow_store

    def _flow(fid, ws, agent):
        flow_store.save_flow({
            "id": fid, "name": fid, "description": "", "workspace": ws, "task_id": None,
            "nodes": [{"id": "n1", "type": "flowNode", "position": {"x": 0, "y": 0},
                       "data": {"label": agent, "agent_id": agent, "description": ""}}],
            "edges": [],
        })
    _flow("flow-a", "ws_a", "agent_a")
    _flow("flow-b", "ws_b", "agent_b")
    yield
    for fid in ("flow-a", "flow-b"):
        flow_store.delete_flow(fid)


def test_flow_tools_answer_another_workspaces_flow_as_missing(flows):
    from flow import store as flow_store
    from tools.flow_management import (
        create_flow_tool, delete_flow_tool, get_flow_tool, modify_flow_tool, validate_flow_tool,
    )
    from tools.langchain_tools import list_flows_tool, run_flow_tool
    with run_in("ws_a"):
        assert call(get_flow_tool, flow_id="flow-a")["ok"] is True
        for out in (call(get_flow_tool, flow_id="flow-b"),
                    call(modify_flow_tool, flow_id="flow-b", description="x"),
                    call(validate_flow_tool, flow_id="flow-b"),
                    call(delete_flow_tool, flow_id="flow-b"),
                    call(run_flow_tool, flow_id="flow-b", user_approved=True)):
            assert out["ok"] is False and out["code"] == "not_found"
        assert [f["id"] for f in call(list_flows_tool)["flows"]] == ["flow-a"]
        # A flow built from another workspace's own agent is refused.
        out = call(create_flow_tool, name="Stolen", nodes=[{"id": "n1", "agent_id": "agent_b"}])
        assert out["code"] == "invalid_flow"
        assert any("agent_b" in e for e in out["errors"])
    assert flow_store.get_flow("flow-b") is not None
    with service():
        assert call(get_flow_tool, flow_id="flow-b")["flow"]["id"] == "flow-b"


# ── tools/world_management.py ────────────────────────────────────────────────

def test_world_tools_answer_another_workspaces_world_as_missing(two_ws):
    from playground import store
    from playground.worlds import WorldSpec, new_world_id
    from tools.world_management import (
        delete_world_tool, get_world_tool, list_worlds_tool, modify_world_tool, validate_world_tool,
    )

    def _world(name, ws):
        return store.save_world(WorldSpec.from_dict({
            "world_id": new_world_id(), "name": name, "workspace": ws,
            "locations": [{"name": "hall"}],
        }))
    mine, theirs, shared = _world("Mine", "ws_a"), _world("Theirs", "ws_b"), _world("Shared", None)
    with run_in("ws_a"):
        assert call(get_world_tool, world_id=mine.world_id)["ok"] is True
        for out in (call(get_world_tool, world_id=theirs.world_id),
                    call(validate_world_tool, world_id=theirs.world_id),
                    call(modify_world_tool, world_id=theirs.world_id, description="x"),
                    call(delete_world_tool, world_id=theirs.world_id)):
            assert out["code"] == "not_found"
        names = {w["name"] for w in call(list_worlds_tool)["worlds"]}
        assert names == {"Mine", "Shared"}
        # A shared world is read everywhere, and changed only from default.
        assert call(get_world_tool, world_id=shared.world_id)["ok"] is True
        assert call(modify_world_tool, world_id=shared.world_id, description="x")["code"] == "not_found"
    assert store.get_world(theirs.world_id) is not None
    with service():
        assert call(get_world_tool, world_id=theirs.world_id)["ok"] is True


# ── tools/scenario_management.py ─────────────────────────────────────────────

def test_scenario_tools_answer_another_workspaces_scenario_as_missing(two_ws):
    from playground import store
    from playground.models import Role, Scenario
    from tools.scenario_management import (
        create_scenario_tool, delete_scenario_tool, get_scenario_tool, list_scenarios_tool,
        modify_scenario_tool,
    )
    mine = store.save_scenario(Scenario(name="Mine", environment="social", workspace="ws_a",
                                        roles=[Role(agent_id="agent_a", name="Ann")]))
    theirs = store.save_scenario(Scenario(name="Theirs", environment="social", workspace="ws_b",
                                          roles=[Role(agent_id="agent_b", name="Bob")]))
    with run_in("ws_a"):
        assert call(get_scenario_tool, scenario_id=mine.scenario_id)["ok"] is True
        for out in (call(get_scenario_tool, scenario_id=theirs.scenario_id),
                    call(modify_scenario_tool, scenario_id=theirs.scenario_id, description="x"),
                    call(delete_scenario_tool, scenario_id=theirs.scenario_id)):
            assert out["code"] == "not_found"
        assert [s["name"] for s in call(list_scenarios_tool)["scenarios"]] == ["Mine"]
        out = call(create_scenario_tool, name="Cast", environment="social",
                   roles=[{"agent_id": "agent_b", "name": "Bob"}])
        assert out["code"] == "invalid_scenario"
    with service():
        assert call(get_scenario_tool, scenario_id=theirs.scenario_id)["ok"] is True


# ── tools/team_management.py ─────────────────────────────────────────────────

def _team(name, ws, agent):
    from teams import store
    from teams.models import Team
    return store.save_team(Team.from_dict({
        "name": name, "workspace": ws, "mode": "parallel", "charter": "c",
        "members": [{"agent_id": agent, "name": agent, "manifest": "does things"}],
    }))


def test_team_tools_answer_another_workspaces_team_as_missing(two_ws):
    from teams import store
    from tools.team_management import (
        create_team_tool, delete_team_tool, get_team_tool, list_teams_tool, modify_team_tool,
    )
    mine, theirs = _team("Mine", "ws_a", "agent_a"), _team("Theirs", "ws_b", "agent_b")
    with run_in("ws_a"):
        assert call(get_team_tool, team_id=mine.team_id)["ok"] is True
        for out in (call(get_team_tool, team_id=theirs.team_id),
                    call(modify_team_tool, team_id=theirs.team_id, charter="x"),
                    call(delete_team_tool, team_id=theirs.team_id)):
            assert out["code"] == "not_found"
        assert [t["name"] for t in call(list_teams_tool)["teams"]] == ["Mine"]
        out = call(create_team_tool, name="Raid", mode="parallel",
                   members=[{"agent_id": "agent_b", "name": "B", "manifest": "m"}])
        assert out["code"] == "invalid_team"
        assert call(create_team_tool, name="Own", mode="parallel",
                    members=[{"agent_id": "agent_a", "name": "A", "manifest": "m"}])["ok"] is True
    assert store.get_team(theirs.team_id) is not None
    with service():
        assert call(get_team_tool, team_id=theirs.team_id)["ok"] is True


# ── tools/loop_management.py ─────────────────────────────────────────────────

def test_loop_tools_answer_another_workspaces_loop_as_missing(flows):
    from loops import store
    from loops.models import Loop
    from tools.loop_management import (
        create_loop_tool, delete_loop_tool, get_loop_tool, list_loops_tool, validate_loop_tool,
    )
    mine = store.save_loop(Loop(name="Mine", workspace="ws_a", flow_id="flow-a", exit_criterion="done"))
    theirs = store.save_loop(Loop(name="Theirs", workspace="ws_b", flow_id="flow-b", exit_criterion="done"))
    with run_in("ws_a"):
        assert call(get_loop_tool, loop_id=mine.loop_id)["ok"] is True
        for out in (call(get_loop_tool, loop_id=theirs.loop_id),
                    call(validate_loop_tool, loop_id=theirs.loop_id),
                    call(delete_loop_tool, loop_id=theirs.loop_id)):
            assert out["code"] == "not_found"
        assert [lp["name"] for lp in call(list_loops_tool)["loops"]] == ["Mine"]
        # A loop that would repeat another workspace's flow is refused.
        out = call(create_loop_tool, name="Borrowed", flow_id="flow-b", exit_criterion="done")
        assert out["code"] == "invalid_loop"
    with service():
        assert call(get_loop_tool, loop_id=theirs.loop_id)["ok"] is True


# ── tools/entity_runs.py ─────────────────────────────────────────────────────

def test_entity_runs_of_another_workspace_are_refused(two_ws, monkeypatch):
    from teams import store
    from teams.models import TeamRun
    import teams.runner as team_runner
    from tools.entity_runs import get_team_run_tool, run_team_tool, stop_team_run_tool

    stopped = []
    monkeypatch.setattr(team_runner, "stop_run", lambda rid: stopped.append(rid) or True)
    mine, theirs = _team("Mine", "ws_a", "agent_a"), _team("Theirs", "ws_b", "agent_b")
    my_run = store.save_run(TeamRun(team_id=mine.team_id, workspace="ws_a", status="running"))
    their_run = store.save_run(TeamRun(team_id=theirs.team_id, workspace="ws_b", status="running"))
    with run_in("ws_a"):
        # Refused before the estimate: the other workspace's team is not shown.
        out = call(run_team_tool, team_id=theirs.team_id, goal="g")
        assert out["ok"] is False and out["code"] == "forbidden" and "estimate" not in out
        assert call(get_team_run_tool, team_run_id=their_run.team_run_id)["code"] == "not_found"
        assert call(get_team_run_tool, team_id=theirs.team_id)["code"] == "not_found"
        assert call(stop_team_run_tool, team_run_id=their_run.team_run_id)["ok"] is False
        assert stopped == []
        assert call(get_team_run_tool, team_run_id=my_run.team_run_id)["ok"] is True
        assert call(stop_team_run_tool, team_run_id=my_run.team_run_id)["ok"] is True
    with service():
        assert call(get_team_run_tool, team_run_id=their_run.team_run_id)["ok"] is True
        assert call(stop_team_run_tool, team_run_id=their_run.team_run_id)["ok"] is True


# ── tools/langchain_tools.py: agents and task coordination ───────────────────

def test_get_agent_only_for_an_agent_available_here(two_ws):
    from tools.langchain_tools import get_agent_tool, list_agents_tool
    with run_in("ws_a"):
        assert call(get_agent_tool, agent_id="agent_a")["agent"]["id"] == "agent_a"
        assert call(get_agent_tool, agent_id="helper")["ok"] is True
        assert call(get_agent_tool, agent_id="agent_b")["code"] == "not_found"
        ids = {a["id"] for a in call(list_agents_tool)["agents"]}
        assert "agent_b" not in ids and "agent_a" in ids
    with service():
        assert call(get_agent_tool, agent_id="agent_b")["ok"] is True


def test_modify_agent_only_for_an_agent_this_workspace_owns(two_ws):
    from agents.registry import get_agent
    from tools.langchain_tools import modify_agent_tool
    with run_in("ws_a"):
        assert call(modify_agent_tool, agent_id="agent_a", description="mine")["ok"] is True
        assert call(modify_agent_tool, agent_id="agent_b", description="x")["code"] == "not_found"
        # Not owned by ws_a (no owner is default's) and a system agent.
        assert call(modify_agent_tool, agent_id="helper", description="x")["code"] == "not_found"
        assert call(modify_agent_tool, agent_id="sysbot", description="x")["code"] == "forbidden"
    assert get_agent("agent_b").description == "agent_b agent"
    assert get_agent("sysbot").description == "sysbot agent"
    with run_in("default"):
        assert call(modify_agent_tool, agent_id="helper", description="default owns it")["ok"] is True
    with service():
        assert call(modify_agent_tool, agent_id="agent_b", description="service")["ok"] is True
    assert get_agent("agent_b").description == "service"


def test_delete_agent_only_for_an_agent_this_workspace_owns(two_ws):
    from agents.registry import get_agent
    from tools.langchain_tools import delete_agent_tool
    with run_in("ws_a"):
        assert call(delete_agent_tool, agent_id="agent_b")["code"] == "not_found"
        assert call(delete_agent_tool, agent_id="sysbot")["code"] == "forbidden"
        assert call(delete_agent_tool, agent_id="agent_a")["ok"] is True
    assert get_agent("agent_b") is not None and get_agent("sysbot") is not None
    assert get_agent("agent_a") is None
    with service():
        assert call(delete_agent_tool, agent_id="agent_b")["ok"] is True


def test_create_agent_cannot_extend_another_workspaces_agent(two_ws):
    from agents.registry import get_agent
    from tools.langchain_tools import create_agent_tool
    with run_in("ws_a"):
        out = call(create_agent_tool, agent_id="copycat", name="Copycat", extends="agent_b")
        assert out["code"] == "not_found"
        assert get_agent("copycat") is None
        out = call(create_agent_tool, agent_id="own_child", name="Own child", extends="agent_a")
        assert out["ok"] is True, out
        assert get_agent("own_child").owner_workspace == "ws_a"


def test_assign_and_follow_only_this_workspaces_tasks_and_agents(tasks):
    from tools.langchain_tools import (
        assign_agent_tool, get_agent_status_tool, reject_assignment_tool, start_agent_tool,
        stop_agent_tool, wait_for_agent_tool,
    )
    b = str(tasks["b"].id)
    with run_in("ws_a"):
        for tool in (start_agent_tool, reject_assignment_tool, stop_agent_tool,
                     get_agent_status_tool, wait_for_agent_tool):
            assert call(tool, task_id=b)["code"] == "not_found", tool.name
        assert call(assign_agent_tool, task_id=b, agent_id="agent_a")["code"] == "not_found"
        # Another workspace's agent is not assignable to this workspace's task.
        out = call(assign_agent_tool, task_id=str(tasks["a"].id), agent_id="agent_b")
        assert out["code"] == "forbidden"
    with service():
        assert call(get_agent_status_tool, task_id=b)["ok"] is True


def test_run_agent_tool_refuses_an_agent_not_available_here(two_ws):
    from tools.langchain_tools import run_agent_tool
    with run_in("ws_a"):
        out = call(run_agent_tool, agent_id="agent_b", input="do it")
        assert out["code"] == "forbidden"


# ── tools/delegation.py (tasks/delegate.py behind it) ────────────────────────

def test_delegation_refuses_a_parent_task_of_another_workspace(tasks):
    from common.agent_context import current_task_id
    from tools.delegation import delegate_task_tool
    token = current_task_id.set(str(tasks["b"].id))
    try:
        with run_in("ws_a"):
            out = call(delegate_task_tool, agent_id="agent_a", input="do it", wait=False)
            assert out["ok"] is False and out["code"] == "not_found"
            out = call(delegate_task_tool, agent_id="agent_b", input="do it", wait=False)
            assert out["ok"] is False
    finally:
        current_task_id.reset(token)
    from tasks.service import list_tasks
    assert not [t for t in list_tasks() if t.parent_id == tasks["b"].id]


def test_delegation_refuses_an_agent_not_available_here(tasks):
    from common.agent_context import current_task_id
    from tools.delegation import delegate_task_tool
    token = current_task_id.set(str(tasks["a"].id))
    try:
        with run_in("ws_a"):
            out = call(delegate_task_tool, agent_id="agent_b", input="do it", wait=False)
            assert out["ok"] is False and out["code"] == "forbidden"
    finally:
        current_task_id.reset(token)


# ── tools/handoff.py ─────────────────────────────────────────────────────────

def test_handoff_checks_the_target_against_the_runs_workspace(two_ws):
    from agents.registry import AgentSpec
    from chat import handoff as handoff_mod
    from tools.handoff import create_handoff_tools
    front = AgentSpec(id="front", name="Front", type="langchain", entrypoint="x",
                      handoffs=["agent_b", "agent_a"])
    tool = create_handoff_tools(front, workspace=None)[0]
    with run_in("ws_a"):
        sink = handoff_mod.HandoffSink(agent_id="front", chain=["front"])
        token = handoff_mod.set_sink(sink)
        try:
            out = json.loads(tool.invoke({"agent_id": "agent_b", "reason": "x"}))
        finally:
            handoff_mod.reset_sink(token)
    assert out["code"] == "forbidden" and sink.intent is None


# ── tools/eval_ops.py ────────────────────────────────────────────────────────

def test_eval_tools_see_only_this_workspace(two_ws):
    from evals import store
    from evals.models import Case, EvalRun, EvalSet
    from tools.eval_ops import (
        add_eval_case_tool, create_eval_tool, estimate_eval_tool, get_eval_run_tool, get_eval_tool,
        list_eval_runs_tool, list_evals_tool, modify_eval_tool, remove_eval_case_tool, run_eval_tool,
    )
    mine = store.save_eval_set(EvalSet(name="Mine", workspace="ws_a", agent_id="agent_a",
                                       cases=[Case(input="hi", expected="hi")]))
    theirs = store.save_eval_set(EvalSet(name="Theirs", workspace="ws_b", agent_id="agent_b",
                                         cases=[Case(input="hi", expected="hi")]))
    their_run = store.save_eval_run(EvalRun(eval_set_id=theirs.eval_set_id, workspace="ws_b"))
    my_run = store.save_eval_run(EvalRun(eval_set_id=mine.eval_set_id, workspace="ws_a"))
    their_team = _team("Theirs", "ws_b", "agent_b")
    with run_in("ws_a"):
        assert call(get_eval_tool, eval_set_id=mine.eval_set_id)["ok"] is True
        sid = theirs.eval_set_id
        for out in (call(get_eval_tool, eval_set_id=sid),
                    call(modify_eval_tool, eval_set_id=sid, name="x"),
                    call(add_eval_case_tool, eval_set_id=sid, input="q"),
                    call(remove_eval_case_tool, eval_set_id=sid, case_id="c"),
                    call(estimate_eval_tool, eval_set_id=sid),
                    call(run_eval_tool, eval_set_id=sid),
                    call(get_eval_run_tool, eval_run_id=their_run.eval_run_id)):
            assert out["code"] == "not_found"
        assert [s["name"] for s in call(list_evals_tool)["eval_sets"]] == ["Mine"]
        runs = call(list_eval_runs_tool)["eval_runs"]
        assert [r["eval_run_id"] for r in runs] == [my_run.eval_run_id]
        # Measuring another workspace's agent or team would run it here.
        assert call(create_eval_tool, name="x", agent_id="agent_b")["code"] == "not_found"
        out = call(estimate_eval_tool, eval_set_id=mine.eval_set_id,
                   configs=[{"target": {"kind": "team", "id": their_team.team_id}}])
        assert out["code"] == "not_found"
        assert call(estimate_eval_tool, eval_set_id=mine.eval_set_id)["ok"] is True
    with service():
        assert call(get_eval_tool, eval_set_id=theirs.eval_set_id)["ok"] is True
        assert call(get_eval_run_tool, eval_run_id=their_run.eval_run_id)["ok"] is True
