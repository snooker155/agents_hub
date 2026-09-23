"""
The audit trail: unit tests on ``common/audit.py`` itself, route tests over
real HTTP for ``dashboard/backend/routes/audit.py`` (the ``single``/``multi``
fixture pattern from ``tests/test_identity_routes.py``), and the two launch
call sites (``agents/agent_launcher.prepare_run``, ``flow/launcher.start_flow_run``).

Route access is not the same for everyone: an admin reads every row, a member
reads only rows where they are the actor or that belong to a workspace they
own. The middleware writes an ``http.<method>`` row for every write request in
``token``/``multi`` mode on its own; nothing here has to construct one by hand
to see it appear.
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap",
                           json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin_headers, username="bob") -> tuple[str, dict]:
    created = client.post("/api/auth/users",
                          json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login",
                          json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


# ── common/audit.py: record, query, prune ────────────────────────────────────

def test_record_and_query_round_trip():
    from common import audit

    row_id = audit.record(
        "widget.create",
        actor={"actor_id": "u1", "actor_kind": "user", "actor_name": "alice"},
        object_type="widget", object_id="w1", workspace="acme", result="ok",
        details={"color": "red"},
    )
    assert row_id is not None

    page = audit.query(action="widget.create")
    assert page["items"][0]["id"] == row_id
    assert page["items"][0]["actor_name"] == "alice"
    assert page["items"][0]["details"] == {"color": "red"}


def test_query_filters_by_actor_workspace_and_action_prefix():
    from common import audit

    audit.record("auth.login", actor={"actor_id": "u1", "actor_kind": "user",
                                      "actor_name": "alice"}, workspace="acme")
    audit.record("auth.logout", actor={"actor_id": "u1", "actor_kind": "user",
                                       "actor_name": "alice"}, workspace="acme")
    audit.record("user.role", actor={"actor_id": "u2", "actor_kind": "user",
                                     "actor_name": "bob"}, workspace="other")

    by_prefix = audit.query(action="auth.")
    assert {r["action"] for r in by_prefix["items"]} == {"auth.login", "auth.logout"}

    by_actor = audit.query(actor="u2")
    assert len(by_actor["items"]) == 1
    assert by_actor["items"][0]["action"] == "user.role"

    by_workspace = audit.query(workspace="other")
    assert len(by_workspace["items"]) == 1
    assert by_workspace["items"][0]["actor_id"] == "u2"

    # workspaces= (a list) is the non-admin route's scoping tool.
    by_workspaces = audit.query(workspaces=["acme"])
    assert {r["action"] for r in by_workspaces["items"]} == {"auth.login", "auth.logout"}
    assert audit.query(workspaces=[])["items"] == []


def test_prune_drops_rows_older_than_retention_and_keeps_the_rest():
    from common import audit
    from common import db

    audit.record("old.event")
    old_id = audit.query(action="old.event")["items"][0]["id"]
    stale = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE audit_log SET at = ? WHERE id = ?", (stale, old_id))
    audit.record("fresh.event")

    removed = audit.prune(365)
    assert removed == 1
    remaining = {r["action"] for r in audit.query()["items"]}
    assert "old.event" not in remaining
    assert "fresh.event" in remaining

    # 0 (the default in an installation that never set AUDIT_RETENTION_DAYS
    # explicitly to something small) keeps everything.
    assert audit.prune(0) == 0


def test_should_log_request_skips_run_state_and_safe_methods():
    from common import audit

    assert audit.should_log_request("POST", "/api/tasks") is True
    assert audit.should_log_request("GET", "/api/tasks") is False
    assert audit.should_log_request("HEAD", "/api/tasks") is False
    assert audit.should_log_request("POST", "/api/run-state/anything") is False
    assert audit.should_log_request("POST", "/api/audit/export") is False
    assert audit.should_log_request("POST", "/not-api/tasks") is False


def test_webhook_fan_out_enqueues_for_an_endpoint_subscribed_to_audit(monkeypatch):
    from common import audit
    from notify import store as notify_store
    from notify import outbound as notify_outbound
    from workspace import create_workspace_folder

    create_workspace_folder("default")
    endpoint = notify_store.create_endpoint("default", {
        "kind": "webhook", "url": "https://example.com/hook", "events": ["audit"],
    })

    dispatched = []
    monkeypatch.setattr(notify_outbound, "dispatch",
                        lambda ep, event: dispatched.append((ep, event)))

    audit.record("workspace.policy", workspace="default",
                 object_type="workspace", object_id="default")

    assert len(dispatched) == 1
    ep, event = dispatched[0]
    assert ep["id"] == endpoint["id"]
    assert event["type"] == "audit"
    assert event["workspace"] == "default"
    assert event["data"]["action"] == "workspace.policy"


def test_webhook_fan_out_skips_an_endpoint_not_subscribed_to_audit(monkeypatch):
    from common import audit
    from notify import store as notify_store
    from notify import outbound as notify_outbound
    from workspace import create_workspace_folder

    create_workspace_folder("default")
    notify_store.create_endpoint("default", {
        "kind": "webhook", "url": "https://example.com/hook", "events": ["notification"],
    })

    dispatched = []
    monkeypatch.setattr(notify_outbound, "dispatch",
                        lambda ep, event: dispatched.append((ep, event)))

    audit.record("workspace.policy", workspace="default")
    assert dispatched == []


# ── routes/audit.py over real HTTP ───────────────────────────────────────────

def test_single_mode_closes_every_audit_route(single, client):
    assert client.get("/api/audit").status_code == 404
    assert client.get("/api/audit/actions").status_code == 404
    assert client.get("/api/audit/export").status_code == 404


def test_multi_mode_requires_authentication(multi, client):
    assert client.get("/api/audit").status_code == 401


def test_admin_sees_the_middleware_http_row_for_a_write_request(multi, client):
    admin = _admin(client)
    created = client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    assert created.status_code == 200

    rows = client.get("/api/audit", params={"action": "http.post"},
                      headers=admin).json()["items"]
    matches = [r for r in rows if r["path"] == "/api/workspaces"]
    assert matches, rows
    assert matches[0]["result"] == "200"


def test_admin_reads_the_actions_list_and_it_includes_a_known_action(multi, client):
    admin = _admin(client)  # records auth.bootstrap
    actions = client.get("/api/audit/actions", headers=admin).json()
    assert "auth.bootstrap" in actions


def test_a_member_sees_their_own_login_but_not_anothers(multi, client):
    admin = _admin(client)
    bob_id, bob = _member(client, admin, username="bob")
    carol_id, _carol = _member(client, admin, username="carol")

    mine = client.get("/api/audit", headers=bob).json()["items"]
    ids_seen = {r["actor_id"] for r in mine}
    assert bob_id in ids_seen
    assert carol_id not in ids_seen
    # Not an admin: reading everything (an admin-only view) is refused, not
    # silently narrowed to nothing.
    assert client.get("/api/auth/users", headers=bob).status_code == 403


def test_an_owner_sees_rows_of_their_own_workspace(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    owner_id, owner = _member(client, admin, username="olivia")
    client.put("/api/workspaces/alpha/members",
              json={"user_id": owner_id, "role": "owner"}, headers=admin)
    bob_id, _bob = _member(client, admin, username="bob")
    client.put("/api/workspaces/alpha/members",
              json={"user_id": bob_id, "role": "viewer"}, headers=owner)

    rows = client.get("/api/audit", params={"workspace": "alpha"},
                      headers=owner).json()["items"]
    assert any(r["action"] == "workspace.member" and r["workspace"] == "alpha"
              for r in rows)


def test_export_streams_csv_with_a_header_row(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)

    resp = client.get("/api/audit/export", params={"format": "csv"}, headers=admin)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader)
    assert header[0] == "id"
    assert "action" in header and "details" in header
    rows = list(reader)
    assert len(rows) >= 1


def test_export_jsonl_is_one_object_per_line(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)

    resp = client.get("/api/audit/export", params={"format": "jsonl"}, headers=admin)
    assert resp.status_code == 200
    lines = [line for line in resp.text.splitlines() if line.strip()]
    assert lines
    import json
    first = json.loads(lines[0])
    assert "action" in first and "at" in first


def test_export_rejects_an_unknown_format(multi, client):
    admin = _admin(client)
    assert client.get("/api/audit/export", params={"format": "xml"},
                      headers=admin).status_code == 400


# ── the launch call sites ────────────────────────────────────────────────────

def test_prepare_run_records_run_launch(monkeypatch):
    """agents/agent_launcher.prepare_run: no HTTP request is in flight (a
    worker may prepare a launch it did not receive as a request), so the
    actor comes from the current-user contextvar."""
    import agents.agent_launcher as launcher
    import agents.registry as registry
    from tasks import service as ts
    from common import audit

    # Nothing in this suite bootstraps a real agents.json; only the truthy
    # check matters to prepare_run (see tests/test_run_sandbox.py).
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())

    t = ts.create_task("audited launch")
    spec = launcher.prepare_run(str(t.id), "swe_agent", None)

    rows = audit.query(action="run.launch")["items"]
    assert rows, "prepare_run must record run.launch"
    row = rows[0]
    assert row["object_type"] == "run"
    assert row["object_id"] == spec["run_id"]
    assert row["details"]["agent_id"] == "swe_agent"
    assert row["details"]["task_id"] == str(t.id)
    assert row["details"]["execution_mode"] == spec["execution_mode"]
    # No signed-in user in this test process: the local operator launched it.
    assert row["actor_kind"] == "local"


def test_start_flow_run_records_flow_launch(monkeypatch):
    """flow/launcher.start_flow_run: same actor rule as the task launcher."""
    import flow.launcher as flow_launcher
    import flow.store as flow_store
    from tasks import service as ts
    from common import audit

    monkeypatch.setattr(flow_store, "get_flow",
                        lambda flow_id: {"id": flow_id, "nodes": [], "edges": []})
    # Never actually spawn runtime/flow_run.py.
    monkeypatch.setattr(flow_launcher, "_spawn_flow_process", lambda *a, **k: 4242)

    t = ts.create_task("audited flow launch")
    run_id, _session_id = flow_launcher.start_flow_run(str(t.id), "flow-1", None)

    rows = audit.query(action="flow.launch")["items"]
    assert rows, "start_flow_run must record flow.launch"
    row = rows[0]
    assert row["object_type"] == "run"
    assert row["object_id"] == run_id
    assert row["details"]["flow_id"] == "flow-1"
    assert row["details"]["task_id"] == str(t.id)
    assert row["actor_kind"] == "local"
