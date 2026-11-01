"""Record checks by route for runs, sessions, chats, instances, services,
tasks, tool approvals and the web log (common/record_kinds/runs.py): the
record's own workspace decides, whatever workspace the request names."""
from __future__ import annotations

import inspect
from types import SimpleNamespace as NS

import pytest

from common import record_access, record_kinds
from common.record_kinds import runs as kinds

#: Each kind's records: ``{suffix: workspace}``, ``none`` naming no workspace.
SPACES = {"beta": "beta", "alpha": "alpha", "none": None}


def _role_in(ws):
    return {"alpha": "editor", "default": "viewer"}.get(ws)


def _check(method, template, **params):
    return record_access.check_route(method=method, template=template, params=params,
                                     named_workspace="alpha", role_in=_role_in)


@pytest.fixture
def stores(monkeypatch):
    """Real rows in the stores that have a cheap create; the team, flow, loop
    and scenario run stores stubbed by id."""
    from common import chat_store, session_service, tool_approvals
    from common.config import settings
    from instances import store as instance_store
    from managers import run_manager
    from services import store as service_store
    from tasks import service as tasks_service
    from tools import web_log

    ids = {}
    for suffix, ws in SPACES.items():
        run_manager.open_run(f"run-{suffix}", "researcher", workspace=ws, link_to_session=False)
        session_service.upsert_context({"session_id": f"sess-{suffix}", "workspace": ws, "events": []})
        chat_store.save_chat({"id": f"chat-{suffix}", "workspace": ws, "messages": []})
        instance_store.create("researcher", instance_id=f"inst-{suffix}", workspace=ws)
        service_store.create(name=f"svc {suffix}", agent_id="researcher", workspace=ws,
                             service_id=f"svc-{suffix}")
        ids[f"task-{suffix}"] = str(tasks_service.create_task(f"task {suffix}", workspace=ws).id)
        # The call's own workspace says alpha: its run's wins when it names
        # one (routes/tool_approvals.py ``_workspace``).
        own = "alpha" if ws else None
        row = tool_approvals.open_approval(run_id=f"run-{suffix}", tool="fetch_url", workspace=own)
        ids[f"appr-{suffix}"] = row["approval_id"]
        proposal = tool_approvals.open_approval(run_id=f"run-{suffix}", tool="propose_connection",
                                                workspace=own)
        ids[f"prop-{suffix}"] = proposal["approval_id"]
    # A call whose run is gone: its own workspace.
    ids["appr-orphan-beta"] = tool_approvals.open_approval(
        run_id="run-gone", tool="fetch_url", workspace="beta")["approval_id"]

    monkeypatch.setattr(settings, "web_log_enabled", True, raising=False)
    for suffix, ws in SPACES.items():
        web_log.append({"id": f"web-{suffix}", "kind": "fetch", "workspace": ws})

    from flow import run_store
    from loops import store as loop_store
    from playground import store as play
    from teams import store as team_store

    team_runs = {f"team-{s}": NS(workspace=ws, team_id="gone") for s, ws in SPACES.items()}
    loop_runs = {f"loop-{s}": NS(workspace=ws, loop_id="gone") for s, ws in SPACES.items()}
    flow_runs = {f"flow-{s}": {"workspace": ws, "flow_id": "gone"} for s, ws in SPACES.items()}
    sims = {f"sim-{s}": NS(workspace=ws, scenario_id="gone") for s, ws in SPACES.items()}
    monkeypatch.setattr(team_store, "get_run", team_runs.get)
    monkeypatch.setattr(team_store, "get_team", lambda _id: None)
    monkeypatch.setattr(loop_store, "get_run", loop_runs.get)
    monkeypatch.setattr(loop_store, "get_loop", lambda _id: None)
    monkeypatch.setattr(run_store, "get_flow_run", flow_runs.get)
    monkeypatch.setattr(play, "get_sim_run", sims.get)
    monkeypatch.setattr(play, "get_scenario", lambda _id: None)
    return ids


def _id(stores, prefix, suffix):
    return stores.get(f"{prefix}-{suffix}", f"{prefix}-{suffix}")


# (read template, write template, param, id prefix)
CASES = [
    ("/api/messages/{run_id}", "/api/messages/{run_id}/stop", "run_id", "run"),
    ("/api/logs/{run_id}", "/api/messages/{run_id}", "run_id", "run"),
    ("/api/browser/runs/{run_id}/session", "/api/messages/{run_id}", "run_id", "run"),
    ("/api/runs/{run_id}/steer", "/api/runs/{run_id}/replay", "run_id", "run"),
    ("/api/runs/{run_id}/steer", "/api/runs/{run_id}/steer", "run_id", "team"),
    ("/api/sessions/{session_id}", "/api/sessions/{session_id}/stop", "session_id", "sess"),
    ("/api/chats/{chat_id}", "/api/chats/{chat_id}", "chat_id", "chat"),
    ("/api/instances/{instance_id}", "/api/instances/{instance_id}/message", "instance_id", "inst"),
    ("/api/services/{service_id}", "/api/services/{service_id}/pause", "service_id", "svc"),
    ("/api/tasks/{task_id}", "/api/tasks/{task_id}/answer", "task_id", "task"),
    ("/api/tasks/{task_id}/outcome", "/api/tasks/{task_id}/outcome", "task_id", "task"),
    ("/api/tool-approvals/{approval_id}", "/api/tool-approvals/{approval_id}", "approval_id", "appr"),
    ("/api/tool-approvals/{approval_id}", "/api/connection-proposals/{approval_id}/apply",
     "approval_id", "prop"),
    ("/api/web-logs/{entry_id}", "/api/web-logs/{entry_id}", "entry_id", "web"),
]


@pytest.mark.parametrize("read,write,param,prefix", CASES)
def test_the_records_own_workspace_decides(stores, read, write, param, prefix):
    beta, alpha, none = (_id(stores, prefix, s) for s in ("beta", "alpha", "none"))
    for method, template in (("GET", read), ("POST", write)):
        refused = _check(method, template, **{param: beta})
        assert isinstance(refused, str) and "beta" in refused
        assert _check(method, template, **{param: alpha}) is None
    # No workspace: default's, where this caller is a viewer.
    assert _check("GET", read, **{param: none}) is None
    refused = _check("POST", write, **{param: none})
    assert isinstance(refused, str) and "default" in refused
    # A record that is not there: the route answers 404 itself.
    assert _check("GET", read, **{param: "missing"}) is None
    assert _check("POST", write, **{param: "missing"}) is None


@pytest.mark.parametrize("kind", ["flow", "loop", "team", "container", "scenario"])
def test_a_run_group_is_its_runs(stores, kind):
    prefix = {"flow": "flow", "loop": "loop", "team": "team", "container": "task", "scenario": "sim"}[kind]
    beta, alpha, none = (_id(stores, prefix, s) for s in ("beta", "alpha", "none"))
    get, stop = "/api/runs/groups/{kind}/{group_id}", "/api/runs/groups/{kind}/{group_id}/stop"
    assert "beta" in _check("GET", get, kind=kind, group_id=beta)
    assert "beta" in _check("POST", stop, kind=kind, group_id=beta)
    assert _check("POST", stop, kind=kind, group_id=alpha) is None
    assert _check("GET", get, kind=kind, group_id=none) is None
    assert "default" in _check("POST", stop, kind=kind, group_id=none)
    assert _check("POST", stop, kind=kind, group_id="missing") is None


def test_an_unknown_group_kind_is_the_routes_400(stores):
    assert _check("GET", "/api/runs/groups/{kind}/{group_id}", kind="nope", group_id="x") is None


def test_a_tool_call_whose_run_is_gone_reads_its_own_workspace(stores):
    assert "beta" in _check("GET", "/api/tool-approvals/{approval_id}",
                            approval_id=stores["appr-orphan-beta"])


def test_a_malformed_task_id_is_no_task(stores):
    assert kinds.RESOLVERS["task"]("not-a-uuid") is None


def test_every_kind_is_covered_and_resolves():
    for key in kinds.ROUTES:
        assert record_kinds.covered(key)
    named = {v for v in kinds.ROUTES.values() if isinstance(v, str)} | {"run_group"}
    assert named == set(kinds.RESOLVERS)


def test_exemptions_are_backed_by_code():
    from common import auth
    from dashboard.backend.routes import browser

    # The ingest routes authenticate themselves: no person's principal is looked up.
    for path in ("/api/ingest/runs/r1/events", "/api/ingest/runs/r1/answer"):
        assert auth.is_open_path("POST", path) and auth.is_open_path("GET", path)
    # The browser service's job is an administrator's.
    assert "_require_admin(request)" in inspect.getsource(browser.service_job)
    # Every browser session route checks the session's own workspace.
    session_routes = [r for r in browser.router.routes
                      if getattr(r, "path", "").startswith("/api/browser/sessions/{session_id}")]
    assert len(session_routes) >= 7
    for route in session_routes:
        source = inspect.getsource(route.endpoint)
        assert "_check(request, " in source or "_check(cast(Request, websocket), " in source
