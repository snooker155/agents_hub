"""
Connection proposals (connectors/proposals.py, tools/connection_setup.py,
dashboard/backend/routes/connection_proposals.py): an agent prepares a
connection, a person finishes it in the chat card.

No model is called and nothing leaves the machine: connector tests and MCP
discovery are stubbed, the database is a SQLite file.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
import uuid

import pytest

from common import stream_sink, tool_approvals
from connectors import proposals


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


def _open_run(run_id: str, *, origin: str = "chat", session_type: str = "chat", owner_ws: str = "acme"):
    from managers import run_manager
    run_manager.open_run(run_id, "main-agent", link_to_session=False, status="running",
                         session_type=session_type, message_origin=origin,
                         task_id=f"conv-{uuid.uuid4().hex[:6]}", workspace=owner_ws)


@pytest.fixture
def key(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "correct horse battery staple", raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)


@pytest.fixture
def jira(monkeypatch):
    from connectors import credentials
    spec = credentials.get("jira")
    monkeypatch.setattr(spec, "test", lambda: {"ok": True, "identity": "ann@acme.test"})
    return spec


def _keys(proposal):
    return {f["key"]: f for f in proposal["fields"]}


# ── building ─────────────────────────────────────────────────────────────────

def test_a_connector_proposal_lists_its_fields_and_leaves_secrets_blank(jira):
    p = proposals.build("connector", "jira", {"base_url": "https://acme.atlassian.net",
                                              "email": "ann@acme.test"})
    assert p["kind"] == "connector" and p["target"] == "jira" and p["scope"] == "everywhere"
    fields = _keys(p)
    assert fields["base_url"]["value"] == "https://acme.atlassian.net"
    assert fields["api_token"]["secret"] and fields["api_token"]["value"] == ""
    assert fields["api_token"]["required"]


def test_the_agent_may_not_fill_a_secret(jira):
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("connector", "jira", {"api_token": "abc"})
    assert err.value.code == "secret_from_agent"
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("database", "warehouse", {"kind": "postgres", "dsn": "postgres://u:p@h/db"},
                        workspace="acme")
    assert err.value.code == "secret_from_agent"


def test_unknown_targets_and_fields_say_what_exists():
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("connector", "jora")
    assert err.value.code == "unknown_target" and "jira" in str(err.value)
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("connector", "jira", {"password": "x"})
    assert err.value.code == "unknown_field"
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("connector", "databases")
    assert "'database' kind" in str(err.value)
    with pytest.raises(proposals.ProposalError):
        proposals.build("teleport", "x")


def test_a_workspace_kind_needs_a_workspace():
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("secret", "API_TOKEN", {})
    assert err.value.code == "no_workspace"


def test_an_mcp_server_claims_everything_and_asks_for_secret_headers():
    p = proposals.build("mcp_server", "tickets", {
        "transport": "stdio", "command": "npx", "args": ["-y", "@acme/mcp"],
        "env": {"GITHUB_TOKEN": "", "LOG_LEVEL": "debug"},
        "capabilities": {"ingests_untrusted": False, "reads_private": False, "can_exfiltrate": False},
    }, workspace="acme")
    fields = _keys(p)
    assert fields["env.GITHUB_TOKEN"]["secret"] and fields["env.LOG_LEVEL"]["value"] == "debug"
    # An agent cannot vouch for a server: every claim stays on.
    assert all(fields[f"capabilities.{c}"]["value"] is True for c in proposals.MCP_CAPABILITIES)
    assert any("npx -y @acme/mcp" in w for w in p["warnings"])
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("mcp_server", "tickets", {"command": "npx", "headers": {"Authorization": "Bearer x"}},
                        workspace="acme")
    assert err.value.code == "secret_from_agent"


def test_a_watcher_asks_for_the_secret_it_names_when_the_workspace_lacks_it(key):
    p = proposals.build("watcher", "inbox", {"kind": "imap", "host": "imap.acme.test",
                                             "username": "ann", "password_secret": "MAIL_PASSWORD"},
                        workspace="acme")
    fields = _keys(p)
    assert fields["secret:MAIL_PASSWORD"]["secret"]
    assert proposals.required_role(p)["role"] == "owner"
    from common import secrets
    secrets.set_secret("acme", "MAIL_PASSWORD", "hunter2")
    again = proposals.build("watcher", "inbox", {"kind": "imap", "host": "imap.acme.test",
                                                 "username": "ann", "password_secret": "MAIL_PASSWORD"},
                            workspace="acme")
    assert "secret:MAIL_PASSWORD" not in _keys(again)
    assert proposals.required_role(again)["role"] == "editor"


def test_options_name_things_but_never_values(key, jira):
    from common import secrets
    secrets.set_secret("acme", "DEPLOY_KEY", "do-not-show")
    out = proposals.options(workspace="acme")
    assert {"connector", "channel", "mcp_server", "database", "watcher", "secret"} <= set(out)
    assert "DEPLOY_KEY" in out["secret"]["existing"]
    assert "do-not-show" not in json.dumps(out)
    jira_opts = next(t for t in out["connector"]["targets"] if t["target"] == "jira")
    assert {"key": "api_token", "secret": True, "required": True} == \
        {k: v for k, v in next(f for f in jira_opts["fields"] if f["key"] == "api_token").items()
         if k in ("key", "secret", "required")}


# ── applying ─────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_applying_a_connector_saves_it_and_reports_the_test(jira):
    p = proposals.build("connector", "jira", {"base_url": "https://acme.atlassian.net",
                                              "email": "ann@acme.test"})
    with pytest.raises(proposals.ProposalError) as err:
        _run(proposals.apply(p, secrets={}))
    assert err.value.code == "missing_secret"
    assert not jira.is_configured()
    out = _run(proposals.apply(p, values={"email": "bob@acme.test"}, secrets={"api_token": "tok-1"}))
    assert out["ok"] and "Test passed as ann@acme.test" in out["summary"]
    assert jira.store.get("api_token") == "tok-1" and jira.store.get("email") == "bob@acme.test"
    # Configured now: a second proposal keeps the stored token when left empty.
    second = proposals.build("connector", "jira", {})
    assert second["replaces"] and _keys(second)["api_token"]["has_value"]
    _run(proposals.apply(second, secrets={}))
    assert jira.store.get("api_token") == "tok-1"


def test_a_secret_the_proposal_does_not_ask_for_is_refused(jira):
    p = proposals.build("connector", "jira", {})
    with pytest.raises(proposals.ProposalError) as err:
        _run(proposals.apply(p, secrets={"api_token": "t", "admin_password": "x"}))
    assert err.value.code == "unknown_field"


def test_applying_a_secret_binds_it_to_its_hosts(key):
    from common import secrets
    p = proposals.build("secret", "STRIPE_KEY", {"allowed_hosts": ["api.stripe.com"]}, workspace="acme")
    out = _run(proposals.apply(p, secrets={"value": "sk_live_1"}))
    assert out["ok"] and "api.stripe.com" in out["summary"]
    assert secrets.get_secret("acme", "STRIPE_KEY") == "sk_live_1"
    assert "sk_live_1" not in out["summary"]


def test_applying_an_mcp_server_attaches_it_with_the_persons_claim(monkeypatch):
    from mcp_client import store as mcp_store
    state = {"settings": {}}
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": dict(state["settings"])})
    monkeypatch.setattr("workspace.update_workspace_metadata",
                        lambda name, updates: state.update(settings=dict(updates.get("settings") or {}))
                        or {"settings": dict(state["settings"])})
    monkeypatch.setattr("mcp_client.client.discover", lambda record: [{"name": "search"}, {"name": "open"}])
    monkeypatch.setattr("mcp_client.client.refresh", lambda ws, sid: None)
    p = proposals.build("mcp_server", "tickets", {"transport": "streamable_http",
                                                  "url": "https://mcp.acme.test/mcp",
                                                  "headers": {"Authorization": ""}}, workspace="acme")
    out = _run(proposals.apply(p, values={"capabilities.can_exfiltrate": False},
                               secrets={"headers.Authorization": "Bearer t"}))
    assert out["ok"] and "2 tool(s)" in out["summary"]
    record = mcp_store.get_server("acme", "tickets")
    assert record["headers"]["Authorization"] == "Bearer t"
    assert record["capabilities"] == {"ingests_untrusted": True, "reads_private": True,
                                      "can_exfiltrate": False}
    with pytest.raises(proposals.ProposalError) as err:
        proposals.build("mcp_server", "tickets", {"command": "x"}, workspace="acme")
    assert err.value.status == 409


def test_applying_a_database_connection_tests_it(tmp_path):
    import sqlite3
    from connectors.databases import store as db_store
    path = tmp_path / "shop.db"
    sqlite3.connect(path).execute("CREATE TABLE t (x INTEGER)").connection.commit()
    p = proposals.build("database", "shop", {"kind": "sqlite"}, workspace="acme")
    out = _run(proposals.apply(p, secrets={"dsn": str(path)}))
    assert out["ok"], out
    assert [c["name"] for c in db_store.list_connections("acme")] == ["shop"]


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from common.auth import Principal
    from routes import connection_proposals, tool_approvals as approval_routes

    app = FastAPI()

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        who = request.headers.get("x-test-principal")
        if who:
            kind, _, rest = who.partition(":")
            uid, _, role = rest.partition(":")
            request.state.principal = Principal(id=uid, username=uid, role=role or "member", kind=kind)
        return await call_next(request)

    app.include_router(approval_routes.router)
    app.include_router(connection_proposals.router)
    return TestClient(app)


def _pending_proposal(jira_spec, owner="u-owner"):
    run_id = _rid()
    _open_run(run_id)
    p = proposals.build("connector", "jira", {"base_url": "https://acme.atlassian.net",
                                              "email": "ann@acme.test"})
    return tool_approvals.open_approval(run_id=run_id, tool=proposals.TOOL, tool_input=p,
                                        reason="to read the sprint", by="setup", owner=owner)


def test_apply_settles_the_waiting_call_with_the_outcome(client, jira):
    row = _pending_proposal(jira)
    listed = client.get("/api/connection-proposals", params={"status": "pending"}).json()["proposals"]
    assert [r["approval_id"] for r in listed] == [row["approval_id"]]
    bad = client.post(f"/api/connection-proposals/{row['approval_id']}/apply", json={"secrets": {}})
    assert bad.status_code == 400 and "api_token" in bad.json()["detail"]
    assert tool_approvals.get(row["approval_id"])["status"] == "pending"
    resp = client.post(f"/api/connection-proposals/{row['approval_id']}/apply",
                       json={"values": {}, "secrets": {"api_token": "s3cr3t-value"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approval"]["status"] == "approved"
    assert body["approval"]["note"] == body["outcome"]["summary"]
    assert "s3cr3t-value" not in json.dumps(body["approval"])
    again = client.post(f"/api/connection-proposals/{row['approval_id']}/apply",
                        json={"secrets": {"api_token": "s3cr3t-value"}})
    assert again.status_code == 409
    from common import audit
    assert audit.query(action="connection.apply")["items"]


def test_a_plain_approve_is_refused_and_deny_works(client, jira):
    row = _pending_proposal(jira)
    resp = client.post(f"/api/tool-approvals/{row['approval_id']}", json={"decision": "approve"})
    assert resp.status_code == 400
    resp = client.post(f"/api/tool-approvals/{row['approval_id']}", json={"decision": "deny", "note": "later"})
    assert resp.status_code == 200 and resp.json()["approval"]["status"] == "denied"
    assert not jira.is_configured()


def test_an_agent_may_not_apply_its_own_proposal(client, jira):
    row = _pending_proposal(jira)
    resp = client.post(f"/api/connection-proposals/{row['approval_id']}/apply",
                       json={"secrets": {"api_token": "s3cr3t-value"}},
                       headers={"x-test-principal": "service:svc:admin"})
    assert resp.status_code == 403
    assert not jira.is_configured()


def test_in_multi_mode_a_secret_needs_the_workspace_owner(client, key, monkeypatch):
    from common import identity
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr("common.access.can_see_workspace", lambda principal, ws: True)
    monkeypatch.setattr(identity, "membership_role", lambda ws, uid: "editor")
    run_id = _rid()
    _open_run(run_id)
    p = proposals.build("secret", "API_TOKEN", {}, workspace="acme")
    row = tool_approvals.open_approval(run_id=run_id, tool=proposals.TOOL, tool_input=p,
                                       workspace="acme", owner="u-owner")
    resp = client.post(f"/api/connection-proposals/{row['approval_id']}/apply",
                       json={"secrets": {"value": "v"}}, headers={"x-test-principal": "user:u-owner:member"})
    assert resp.status_code == 403
    assert tool_approvals.get(row["approval_id"])["status"] == "pending"


# ── the agent's tools ────────────────────────────────────────────────────────

@pytest.fixture
def chat_turn(monkeypatch):
    run_id = _rid()
    _open_run(run_id)
    from agents.agent_loop import LoopState, reset_state, set_state
    state_token = set_state(LoopState(run_id=run_id, agent_id="main-agent", workspace="acme"))
    monkeypatch.setattr(tool_approvals, "POLL_SECONDS", 0.02)
    events: list = []
    token = stream_sink.set_emitter(events.append)
    yield run_id, events
    stream_sink.reset_emitter(token)
    reset_state(state_token)


def _start(fn, **kwargs):
    out: dict = {}
    ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: out.update(result=ctx.run(fn, **kwargs)), daemon=True)
    thread.start()
    return thread, out


def _wait_for(events, kind, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in events:
            if e.get("type") == kind:
                return e
        time.sleep(0.01)
    raise AssertionError(f"no {kind} event in {events}")


def test_in_the_chat_the_tool_waits_for_the_card_and_reads_the_outcome(chat_turn, jira):
    from tools.connection_setup import propose_connection
    run_id, events = chat_turn
    thread, out = _start(propose_connection.invoke, input={
        "kind": "connector", "target": "jira",
        "fields": {"base_url": "https://acme.atlassian.net", "email": "ann@acme.test"},
        "reason": "to read the sprint"})
    card = _wait_for(events, "tool_approval")
    assert card["tool"] == "propose_connection" and card["input"]["target"] == "jira"
    row = tool_approvals.get(card["approval_id"])
    outcome = _run(proposals.apply(row["input"], secrets={"api_token": "s3cr3t-value"}))
    tool_approvals.decide(card["approval_id"], "approve", note=outcome["summary"], author="ann")
    thread.join(5)
    result = json.loads(out["result"])
    assert result["ok"] and result["status"] == "applied" and "Test passed" in result["outcome"]
    assert "s3cr3t-value" not in out["result"]


def test_a_denied_proposal_reads_back_the_note(chat_turn, jira):
    from tools.connection_setup import propose_connection
    run_id, events = chat_turn
    thread, out = _start(propose_connection.invoke, input={"kind": "connector", "target": "jira"})
    card = _wait_for(events, "tool_approval")
    tool_approvals.decide(card["approval_id"], "deny", note="not this one")
    thread.join(5)
    result = json.loads(out["result"])
    assert not result["ok"] and result["code"] == "denied" and "not this one" in result["error"]


def test_outside_the_chat_the_proposal_waits_on_the_connectors_page(jira):
    from agents.agent_loop import LoopState, reset_state, set_state
    from tools.connection_setup import connection_proposal_status, propose_connection
    run_id = _rid()
    _open_run(run_id, origin="telegram")
    token = set_state(LoopState(run_id=run_id, agent_id="main-agent", workspace="acme"))
    try:
        result = json.loads(propose_connection.invoke({"kind": "connector", "target": "jira"}))
        assert result["ok"] and result["status"] == "pending"
        status = json.loads(connection_proposal_status.invoke({"proposal_id": result["proposal_id"]}))
        assert status["status"] == "pending"
        refused = json.loads(propose_connection.invoke({"kind": "connector", "target": "jira",
                                                         "fields": {"api_token": "x"}}))
        assert not refused["ok"] and refused["code"] == "secret_from_agent"
    finally:
        reset_state(token)
    rows = tool_approvals.list_for_tool(proposals.TOOL, status="pending")
    assert [r["approval_id"] for r in rows] == [result["proposal_id"]]


def test_the_tools_are_wired_for_the_main_agent():
    from tools.approval import NEVER_GATED
    from tools.capabilities import grants_of
    from tools.connector_tools import connector_tools
    from tools.registry import get_tool_by_id
    names = {t.name for t in connector_tools()}
    assert {"connection_options", "propose_connection", "connection_proposal_status"} <= names
    assert get_tool_by_id("propose_connection").category == "connectors"
    assert grants_of("propose_connection") == frozenset()
    assert "propose_connection" in NEVER_GATED
    from pathlib import Path
    seed = json.loads((Path(__file__).resolve().parents[1] / "bootstrap" / "agents.json").read_text())
    main = next(a for a in seed["agents"] if a["id"] == "main-agent")
    assert {"connection_options", "propose_connection", "connection_proposal_status"} <= set(main["tools"])


def test_the_routes_are_mounted_on_the_app(jira):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    row = _pending_proposal(jira)
    client = TestClient(app)
    listed = client.get("/api/connection-proposals", params={"status": "pending"})
    assert listed.status_code == 200 and listed.json()["proposals"][0]["approval_id"] == row["approval_id"]
    resp = client.post(f"/api/connection-proposals/{row['approval_id']}/apply",
                       json={"secrets": {"api_token": "s3cr3t-value"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"]["ok"]


def test_a_proposal_past_its_deadline_reads_as_expired(jira):
    from tools.connection_setup import connection_proposal_status
    row = tool_approvals.open_approval(run_id=_rid(), tool=proposals.TOOL,
                                       tool_input=proposals.build("connector", "jira", {}), timeout_s=-1)
    result = json.loads(connection_proposal_status.invoke({"proposal_id": row["approval_id"]}))
    assert not result["ok"] and result["code"] == "expired"


def test_a_connector_proposed_in_a_workspace_lives_there_only(jira):
    from connectors.channels.store import in_workspace
    p = proposals.build("connector", "jira", {"base_url": "https://team.atlassian.net",
                                              "email": "t@team.test"}, workspace="team-a")
    assert p["workspace"] == "team-a" and p["scope"] == "workspace"
    assert proposals.required_role(p) == {"workspace": "team-a", "role": "editor"}
    out = _run(proposals.apply(p, secrets={"api_token": "team-token"}))
    assert "for workspace team-a" in out["summary"]
    assert in_workspace("team-a", jira.store.get, "api_token") == "team-token"
    assert in_workspace("team-b", jira.store.get, "api_token") == ""
    assert jira.store.for_workspace("default").get("api_token") == ""
