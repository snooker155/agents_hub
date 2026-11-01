"""A record that names no workspace is the default workspace's: its members and
an administrator (anyone who sees every workspace) reach it, nobody else does,
and it never shows up in another workspace's listing.

The principal is stubbed the way tests/test_stats_scope.py does it:
``access.visible_workspaces`` returns ``{"default"}`` for a member of default,
``{"alpha"}`` for a member of another workspace and ``None`` for an
administrator.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from common import access, identity, record_access

MEMBER = {"default"}
OTHER = {"alpha"}
ADMIN = None


def _as(monkeypatch, visible):
    """Every route and helper reads the caller through these two."""
    monkeypatch.setattr(identity, "request_principal", lambda request: None)
    monkeypatch.setattr(access, "visible_workspaces", lambda principal: visible)
    monkeypatch.setattr(record_access, "visible_names", lambda request: visible)
    monkeypatch.setattr(record_access, "restricted", lambda principal: visible is not None)


REQ = SimpleNamespace(state=SimpleNamespace())


# ── helpers ──────────────────────────────────────────────────────────────────

def test_access_helpers_read_unbound_as_default(monkeypatch):
    rows = [{"id": 1, "workspace": "default"}, {"id": 2, "workspace": None}, {"id": 3},
            {"id": 4, "workspace": "alpha"}]
    monkeypatch.setattr(access, "visible_workspaces", lambda principal: MEMBER)
    assert access.can_see_workspace(None, "default")
    assert access.can_see_workspace(None, None)
    assert access.can_see_workspace(None, "")
    assert [r["id"] for r in access.filter_by_workspace(None, rows)] == [1, 2, 3]
    assert record_access.visible(MEMBER, None)
    monkeypatch.setattr(access, "visible_workspaces", lambda principal: OTHER)
    assert not access.can_see_workspace(None, None)
    assert not access.can_see_workspace(None, "")
    assert [r["id"] for r in access.filter_by_workspace(None, rows)] == [4]
    assert not record_access.visible(OTHER, None)
    monkeypatch.setattr(access, "visible_workspaces", lambda principal: ADMIN)
    assert access.can_see_workspace(None, None)
    assert len(access.filter_by_workspace(None, rows)) == 4
    assert record_access.visible(ADMIN, None)


# ── runs ─────────────────────────────────────────────────────────────────────

RUNS = [
    {"run_id": "d", "workspace": "default", "agent_id": "x", "status": "completed"},
    {"run_id": "n", "workspace": None, "agent_id": "x", "status": "completed"},
    {"run_id": "e", "workspace": "", "agent_id": "x", "status": "completed"},
]


@pytest.mark.parametrize("visible, expected", [(MEMBER, ["d", "e", "n"]), (ADMIN, ["d", "e", "n"])])
def test_message_list_named_default(monkeypatch, visible, expected):
    from dashboard.backend.routes import messages
    _as(monkeypatch, visible)
    monkeypatch.setattr(messages.run_manager, "query_runs",
                        lambda **kw: {"items": [dict(r) for r in RUNS], "total": 3,
                                      "limit": 200, "offset": 0})
    monkeypatch.setattr(messages, "_enrich_page", lambda runs: runs)
    out = asyncio.run(messages.list_messages(REQ, workspace="default"))
    assert sorted(r["run_id"] for r in out["items"]) == expected


def test_single_run_and_its_trail_refuse_a_member_of_another_workspace(monkeypatch):
    from dashboard.backend.routes import messages
    _as(monkeypatch, OTHER)
    monkeypatch.setattr(messages.run_manager, "get_run_by_id", lambda rid: dict(RUNS[1]))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(messages.get_message("n", REQ))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        asyncio.run(messages.get_message_trail("n", REQ))


def test_single_run_of_no_workspace_is_open_to_a_default_member(monkeypatch):
    from dashboard.backend.routes import messages
    _as(monkeypatch, MEMBER)
    monkeypatch.setattr(messages.run_manager, "get_run_by_id", lambda rid: dict(RUNS[1]))
    try:
        asyncio.run(messages.get_message("n", REQ))
    except HTTPException as exc:
        assert exc.status_code != 403


def test_cost_reports_count_unbound_runs_as_default_runs():
    from common.costs_report import _counted_runs
    runs = [dict(r) for r in RUNS]
    assert len(list(_counted_runs(runs, None, None, None, None, {"default"}))) == 3
    assert list(_counted_runs(runs, None, None, None, None, {"alpha"})) == []
    assert len(list(_counted_runs(runs, None, None, None, None, None))) == 3


# ── sessions ─────────────────────────────────────────────────────────────────

def test_session_list_named_default_keeps_unbound_for_a_member(monkeypatch):
    from dashboard.backend.routes import sessions
    _as(monkeypatch, MEMBER)
    items = [{"session_id": "s1", "workspace": "default"}, {"session_id": "s2", "workspace": None}]
    monkeypatch.setattr(sessions, "_session_service_query",
                        lambda **kw: {"items": [dict(i) for i in items], "total": 2,
                                      "limit": 200, "offset": 0})
    monkeypatch.setattr(sessions.run_manager, "session_run_stats", lambda ids=None: {})
    monkeypatch.setattr(sessions, "_enrich_context", lambda c, stats: c)
    out = asyncio.run(sessions.list_sessions(REQ, workspace="default"))
    assert [i["session_id"] for i in out["items"]] == ["s1", "s2"]


# ── instances ────────────────────────────────────────────────────────────────

def test_instances_list_and_single_get(monkeypatch):
    from dashboard.backend.routes import instances
    _as(monkeypatch, MEMBER)
    rows = [{"instance_id": "i1", "workspace": "default"}, {"instance_id": "i2", "workspace": None}]
    monkeypatch.setattr(instances.store, "list_instances",
                        lambda **kw: {"items": [dict(r) for r in rows], "total": 2,
                                      "limit": 100, "offset": 0})
    monkeypatch.setattr(instances.store, "counts_by_state", lambda **kw: {})
    monkeypatch.setattr(instances.carrier, "sync", lambda i: i)
    monkeypatch.setattr(instances, "_enrich", lambda i, *a, **kw: i)
    monkeypatch.setattr(instances, "_service_names", lambda items: {})
    out = asyncio.run(instances.list_instances(REQ, workspace="default"))
    assert [i["instance_id"] for i in out["items"]] == ["i1", "i2"]

    monkeypatch.setattr(instances.store, "get", lambda iid: dict(rows[1]))
    try:
        asyncio.run(instances.get_instance("i2", REQ))
    except HTTPException as exc:
        assert exc.status_code != 403
    _as(monkeypatch, OTHER)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(instances.get_instance("i2", REQ))
    assert exc.value.status_code == 403


# ── views ────────────────────────────────────────────────────────────────────

def test_views_list_and_single_get(monkeypatch):
    from dashboard.backend.routes import views
    _as(monkeypatch, MEMBER)
    rows = [{"view_id": "v1", "workspace": "default"}, {"view_id": "v2", "workspace": None}]
    monkeypatch.setattr(views, "list_views", lambda **kw: [dict(r) for r in rows])
    out = asyncio.run(views.get_views(REQ, workspace=None))
    assert [v["view_id"] for v in out["views"]] == ["v1", "v2"]

    monkeypatch.setattr(views, "get_view", lambda vid: {"view_id": vid, "workspace": None})
    assert asyncio.run(views.get_one_view("v2", REQ))["view_id"] == "v2"
    _as(monkeypatch, OTHER)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(views.get_one_view("v2", REQ))
    assert exc.value.status_code == 403

    _as(monkeypatch, ADMIN)
    assert asyncio.run(views.get_one_view("v2", REQ))["view_id"] == "v2"


# ── tasks ────────────────────────────────────────────────────────────────────

def test_tasks_list_and_single_reads(monkeypatch):
    from dashboard.backend.routes import tasks as task_routes
    _as(monkeypatch, MEMBER)
    bound = SimpleNamespace(id=uuid.uuid4(), workspace="default", parent_id=None)
    unbound = SimpleNamespace(id=uuid.uuid4(), workspace=None, parent_id=None)
    monkeypatch.setattr(task_routes.tasks_service, "list_tasks", lambda: [bound, unbound])
    monkeypatch.setattr(task_routes, "task_to_dict", lambda t: {"id": str(t.id)})
    out = asyncio.run(task_routes.list_tasks(REQ))
    assert out == [{"id": str(bound.id)}, {"id": str(unbound.id)}]

    monkeypatch.setattr(task_routes.tasks_service, "get_task", lambda tid: unbound)
    assert task_routes._visible_task(unbound.id, REQ) is unbound
    _as(monkeypatch, OTHER)
    for route in (task_routes.get_task, task_routes.get_task_result,
                  task_routes.get_task_execution_log, task_routes.get_task_activity_log):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route(unbound.id, REQ))
        assert exc.value.status_code == 403


# ── eval sets ────────────────────────────────────────────────────────────────

def _eval_set(name, workspace):
    from evals import store
    from evals.models import Case, EvalSet, GraderSpec
    return store.save_eval_set(EvalSet(name=name, workspace=workspace,
                                       cases=[Case(case_id="c1", input="q")],
                                       graders=[GraderSpec("substring")]))


def test_strict_eval_set_listing_keeps_unbound_sets_in_default_only():
    from evals import store
    _eval_set("unbound", None)
    _eval_set("mine", "ws1")
    _eval_set("dflt", "default")
    assert {e.name for e in store.list_eval_sets("ws1", strict=True)} == {"mine"}
    assert {e.name for e in store.list_eval_sets("default", strict=True)} >= {"unbound", "dflt"}
    # The agent tools keep the older reading: a set with no workspace everywhere.
    assert "unbound" in {e.name for e in store.list_eval_sets("ws1")}


def test_eval_routes_read_unbound_sets_as_default(monkeypatch):
    from dashboard.backend.routes import evals
    unbound = _eval_set("unbound-route", None)
    _eval_set("dflt-route", "default")
    _as(monkeypatch, MEMBER)
    monkeypatch.setattr(identity, "require_role", lambda *a, **kw: None)
    names = {e["name"] for e in asyncio.run(evals.list_evals("default", REQ))["eval_sets"]}
    assert {"dflt-route", "unbound-route"} <= names
    assert asyncio.run(evals.get_eval(unbound.eval_set_id, REQ))
    _as(monkeypatch, OTHER)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(evals.get_eval(unbound.eval_set_id, REQ))
    assert exc.value.status_code == 403


def test_strict_playground_listings():
    from playground import store
    from playground.models import Role, Scenario
    role = [Role(agent_id="a", name="Alice")]
    store.save_scenario(Scenario(name="unbound-sc", environment="market", roles=role))
    store.save_scenario(Scenario(name="w1-sc", environment="market", roles=role, workspace="w1"))
    assert {s.name for s in store.list_scenarios("w1", strict=True)} == {"w1-sc"}
    assert "unbound-sc" in {s.name for s in store.list_scenarios("default", strict=True)}
    assert "unbound-sc" in {s.name for s in store.list_scenarios("w1")}


# ── flows, teams, loops ──────────────────────────────────────────────────────

def test_flow_list_reads_unbound_flows_as_default(monkeypatch):
    from dashboard.backend.routes import flows
    flows_on_disk = [{"id": "f1", "workspace": "default"}, {"id": "f2", "workspace": None},
                     {"id": "f3", "workspace": None, "shared": True}]
    monkeypatch.setattr(flows, "get_workspace_metadata", lambda ws: {})
    monkeypatch.setattr(flows.flow_store, "list_flows", lambda **kw: [dict(f) for f in flows_on_disk])
    monkeypatch.setattr(flows, "_public_flow", lambda f: f)
    _as(monkeypatch, MEMBER)
    out = asyncio.run(flows.list_flows(workspace="default", request=REQ))
    assert sorted(f["id"] for f in out) == ["f1", "f2", "f3"]
    _as(monkeypatch, ADMIN)
    out = asyncio.run(flows.list_flows(workspace="default", request=REQ))
    assert sorted(f["id"] for f in out) == ["f1", "f2", "f3"]
    # Another workspace's listing never holds a flow that names none.
    flows_on_disk.append({"id": "f4", "workspace": "alpha"})
    _as(monkeypatch, OTHER)
    out = asyncio.run(flows.list_flows(workspace="alpha", request=REQ))
    assert sorted(f["id"] for f in out) == ["f4"]


def test_team_list_named_default_keeps_unbound_teams_for_a_member(monkeypatch):
    from dashboard.backend.routes import teams
    rows = [SimpleNamespace(team_id="t1", workspace="default"),
            SimpleNamespace(team_id="t2", workspace=None)]
    monkeypatch.setattr(teams.store, "list_teams", lambda ws=None: list(rows))
    monkeypatch.setattr(teams, "_enrich", lambda t: {"team_id": t.team_id})
    _as(monkeypatch, MEMBER)
    out = asyncio.run(teams.list_teams(workspace="default", request=REQ))
    assert [t["team_id"] for t in out["teams"]] == ["t1", "t2"]
    _as(monkeypatch, OTHER)
    out = asyncio.run(teams.list_teams(workspace="alpha", request=REQ))
    assert out["teams"] == []


def test_orphaned_unbound_run_resolves_to_default(monkeypatch):
    from loops import store as loop_store
    monkeypatch.setattr(loop_store, "get_run",
                        lambda rid: SimpleNamespace(workspace=None, loop_id="gone"))
    monkeypatch.setattr(loop_store, "get_loop", lambda lid: None)
    record = record_access.resolve("loop_run", "r1")
    assert record is not None and record.unbound
    assert record.workspace == "default"
    assert record_access.decide(method="GET", kind="loop_run", rest=[], record=record,
                                named_workspace=None,
                                role_in=lambda ws: "viewer" if ws == "default" else None) is None
    assert record_access.decide(method="GET", kind="loop_run", rest=[], record=record,
                                named_workspace=None, role_in=lambda ws: None) is not None


# ── audit ────────────────────────────────────────────────────────────────────

def test_audit_member_keeps_their_own_unbound_rows(monkeypatch):
    """A sign-in names no workspace: its actor reads it, as before."""
    from dashboard.backend.routes import audit as audit_routes
    by_workspace = {"items": [{"id": 2, "workspace": "default", "actor_id": "v"}], "total": 1}
    by_actor = {"items": [{"id": 1, "workspace": None, "actor_id": "u"}], "total": 1}
    monkeypatch.setattr(audit_routes.audit, "query",
                        lambda **kw: by_actor if kw.get("actor") else by_workspace)
    monkeypatch.setattr(audit_routes, "_owned_workspaces", lambda principal: ["default"])
    member = SimpleNamespace(id="u", is_admin=False)
    page = audit_routes._accessible_page(member, {"actor": None}, 10, 0)
    assert sorted(r["id"] for r in page["items"]) == [1, 2]
    admin = SimpleNamespace(id="a", is_admin=True)
    monkeypatch.setattr(audit_routes.audit, "query",
                        lambda **kw: {"items": by_workspace["items"] + by_actor["items"], "total": 2})
    assert [r["id"] for r in audit_routes._accessible_page(admin, {}, 10, 0)["items"]] == [2, 1]


# ── plan jobs ────────────────────────────────────────────────────────────────

def test_job_list_and_single_get(monkeypatch):
    from dashboard.backend.routes import plan
    jobs = [SimpleNamespace(id=uuid.uuid4(), workspace="default"),
            SimpleNamespace(id=uuid.uuid4(), workspace=None)]
    monkeypatch.setattr(plan.plan_service, "list_jobs", lambda workspace=None: list(jobs))
    monkeypatch.setattr(plan, "job_to_dict", lambda j: {"id": str(j.id)})
    _as(monkeypatch, MEMBER)
    out = asyncio.run(plan.list_jobs(REQ))
    assert out == [{"id": str(j.id)} for j in jobs]
    monkeypatch.setattr(plan.plan_service, "get_job", lambda jid: jobs[1])
    assert asyncio.run(plan.get_job(jobs[1].id, REQ)) == {"id": str(jobs[1].id)}
    _as(monkeypatch, OTHER)
    assert asyncio.run(plan.list_jobs(REQ)) == []
    with pytest.raises(HTTPException):
        asyncio.run(plan.get_job(jobs[1].id, REQ))



def test_assistant_lookup_reads_unbound_records_as_default():
    from types import SimpleNamespace as NS
    from chat import lookup
    member = NS(reachable=["default", "alpha"], workspace=None)
    outsider = NS(reachable=["alpha"], workspace=None)
    assert lookup._visible(member, None) is True
    assert lookup._visible(outsider, None) is False
    assert lookup._visible(NS(reachable=["default", "alpha"], workspace="alpha"), "") is False
