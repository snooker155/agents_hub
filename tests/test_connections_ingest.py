"""Observe mode: a run the hub never started, reported in and recorded.

The promise being tested is that a reported run is indistinguishable from a
local one once it lands — same run record, same session, same tokens and cost,
same live events — and that the token which lets an outside service report
cannot do anything else.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from common.agent_frames import FrameTranslator
from connections import service as ingest_service
from connections import store as connection_store
from managers import run_manager


@pytest.fixture(autouse=True)
def isolated_connections(tmp_path, monkeypatch):
    """A connections file per test, and no in-memory state carried between them."""
    monkeypatch.setattr(connection_store, "CONNECTIONS_FILE", tmp_path / "connections.json")
    monkeypatch.setattr(connection_store, "CONNECTIONS_LOCK", tmp_path / "connections.json.lock")
    connection_store._cache = None
    connection_store._cache_stamp = None
    ingest_service.reset_for_tests()
    yield
    ingest_service.reset_for_tests()


@pytest.fixture
def api_client():
    """A TestClient carrying only the two routers this feature adds."""
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import connections as connections_routes
    from routes import ingest as ingest_routes

    app = FastAPI()
    app.include_router(connections_routes.router)
    app.include_router(ingest_routes.router)
    return TestClient(app)


@pytest.fixture
def connected(api_client):
    """A created connection and the headers that authenticate as it."""
    resp = api_client.post("/api/connections", json={
        "id": "billing-graph", "name": "Billing graph", "kind": "langgraph",
    })
    assert resp.status_code == 201
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}, token


# ── the token ───────────────────────────────────────────────────────────────

def test_the_token_is_returned_once_and_never_stored(api_client):
    """A leaked connections.json must not let anyone report runs."""
    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]

    stored = connection_store.CONNECTIONS_FILE.read_text(encoding="utf-8")
    assert token not in stored, "the raw token reached disk"

    listed = api_client.get("/api/connections").json()["connections"][0]
    assert "token" not in listed and "token_hash" not in listed
    assert listed["token_hint"] == token[-4:], "the hint is enough to tell two apart"


def test_a_rotated_token_stops_working_immediately(api_client, connected):
    headers, old_token = connected

    api_client.post("/api/connections/billing-graph/rotate")

    refused = api_client.post("/api/ingest/runs", json={"input": "hi"}, headers=headers)
    assert refused.status_code == 401


def test_a_disabled_connection_cannot_report(api_client, connected):
    headers, _ = connected

    api_client.patch("/api/connections/billing-graph", json={"disabled": True})

    assert api_client.post("/api/ingest/runs", json={"input": "hi"}, headers=headers).status_code == 401


def test_no_token_and_a_wrong_token_are_refused_the_same_way(api_client, connected):
    """Different answers would tell an attacker which tokens exist."""
    no_token = api_client.post("/api/ingest/runs", json={"input": "hi"})
    bad_token = api_client.post("/api/ingest/runs", json={"input": "hi"},
                                headers={"Authorization": "Bearer ahc_not-a-real-token"})

    assert no_token.status_code == bad_token.status_code == 401
    assert no_token.json()["detail"] == bad_token.json()["detail"]


def test_the_ingest_prefix_is_exempt_from_the_operator_token_but_lookalikes_are_not():
    """`/api/ingest` carries its own credential; nothing else may inherit that."""
    from common.auth import is_authorized

    def allowed(path):
        return is_authorized(configured_token="operator-token", method="POST", path=path)

    assert allowed("/api/ingest/runs") is True
    assert allowed("/api/ingest") is True
    assert allowed("/api/ingestion-report") is False, "prefix matching must stop at a boundary"
    assert allowed("/api/connections") is False, "managing connections is the operator's"


# ── a reported run ──────────────────────────────────────────────────────────

def test_a_reported_run_becomes_an_ordinary_run_record(api_client, connected):
    headers, _ = connected

    opened = api_client.post("/api/ingest/runs", json={
        "input": "what does the team plan cost?", "model": "gpt-4o-mini", "provider": "openai",
    }, headers=headers).json()
    run_id = opened["run_id"]

    api_client.post(f"/api/ingest/runs/{run_id}/events", json={"events": [
        {"type": "node_start", "node": "triage"},
        {"type": "token", "token": "checking "},
        {"type": "tool_start", "name": "lookup_pricing", "input": "team"},
        {"type": "tool_end", "name": "lookup_pricing", "output": "$99/mo"},
        {"type": "node_end", "node": "triage", "ok": True, "next": "answer"},
        {"type": "token", "token": "the team plan is $99/mo"},
        {"type": "usage", "input_tokens": 120, "output_tokens": 30},
    ]}, headers=headers)

    closed = api_client.post(f"/api/ingest/runs/{run_id}/close",
                             json={"ok": True}, headers=headers).json()

    assert closed["status"] == "completed"
    assert closed["usage"]["total_tokens"] == 150

    record = run_manager.get_runs_by_ids([run_id])[run_id]
    assert record["agent_id"] == "billing-graph"
    assert record["status"] == "completed"
    assert record["session_id"] == opened["session_id"]
    # The cost columns are the point: a run with no tokens is a run nobody can
    # bill, and the reporting agent is the only party that knows them.
    assert record["process"]["token_usage"] == {
        "inbound_tokens": 120, "outbound_tokens": 30, "total_tokens": 150, "cached_tokens": 0,
    }
    # The tool trail and the path through the graph survive the live events.
    payload = run_manager.get_run_process(run_id)
    assert [c["tool"] for c in payload["tool_calls"]] == ["lookup_pricing"]


def test_the_close_body_wins_over_what_was_streamed(api_client, connected):
    """A graph may post-process its own answer; its last word is the record."""
    headers, _ = connected
    run_id = api_client.post("/api/ingest/runs", json={"input": "go"}, headers=headers).json()["run_id"]

    api_client.post(f"/api/ingest/runs/{run_id}/events", json={"events": [
        {"type": "token", "token": "draft draft"},
    ]}, headers=headers)
    api_client.post(f"/api/ingest/runs/{run_id}/close",
                    json={"ok": True, "output": "the final, tidied answer"}, headers=headers)

    record = run_manager.get_runs_by_ids([run_id])[run_id]
    assert record["output"] == "the final, tidied answer"


def test_a_failed_run_is_recorded_as_failed_with_its_reason(api_client, connected):
    headers, _ = connected
    run_id = api_client.post("/api/ingest/runs", json={"input": "go"}, headers=headers).json()["run_id"]

    api_client.post(f"/api/ingest/runs/{run_id}/close",
                    json={"ok": False, "error": "the pricing service timed out"}, headers=headers)

    record = run_manager.get_runs_by_ids([run_id])[run_id]
    assert record["status"] == "failed"
    assert "timed out" in record["error"]


def test_runs_sharing_a_thread_share_one_session(api_client, connected):
    """A client's own conversation id groups its runs the way a chat does."""
    headers, _ = connected

    first = api_client.post("/api/ingest/runs", json={"input": "one", "thread": "ticket-42"},
                            headers=headers).json()
    second = api_client.post("/api/ingest/runs", json={"input": "two", "thread": "ticket-42"},
                             headers=headers).json()
    unrelated = api_client.post("/api/ingest/runs", json={"input": "three"}, headers=headers).json()

    assert first["session_id"] == second["session_id"]
    assert unrelated["session_id"] != first["session_id"]


# ── what a connection may not do ────────────────────────────────────────────

def test_one_connection_cannot_report_into_another_connections_run(api_client, connected):
    headers, _ = connected
    run_id = api_client.post("/api/ingest/runs", json={"input": "go"}, headers=headers).json()["run_id"]

    other = api_client.post("/api/connections", json={"id": "other", "name": "Other"}).json()["token"]
    stolen = api_client.post(
        f"/api/ingest/runs/{run_id}/events",
        json={"events": [{"type": "token", "token": "mine now"}]},
        headers={"Authorization": f"Bearer {other}"},
    )

    assert stolen.status_code == 404


def test_an_unknown_run_is_refused(api_client, connected):
    headers, _ = connected

    resp = api_client.post("/api/ingest/runs/ing-nope/events",
                           json={"events": []}, headers=headers)

    assert resp.status_code == 404


def test_an_oversized_batch_is_refused_rather_than_truncated(api_client, connected):
    """Silently dropping the tail would make a run's trail quietly wrong."""
    headers, _ = connected
    run_id = api_client.post("/api/ingest/runs", json={"input": "go"}, headers=headers).json()["run_id"]

    too_many = [{"type": "token", "token": "x"}] * (ingest_service.MAX_EVENTS_PER_REQUEST + 1)
    resp = api_client.post(f"/api/ingest/runs/{run_id}/events",
                           json={"events": too_many}, headers=headers)

    assert resp.status_code == 413


def test_a_connection_cannot_open_unbounded_runs(api_client, connected, monkeypatch):
    headers, _ = connected
    monkeypatch.setattr(ingest_service, "MAX_OPEN_RUNS", 2)

    api_client.post("/api/ingest/runs", json={"input": "a"}, headers=headers)
    api_client.post("/api/ingest/runs", json={"input": "b"}, headers=headers)
    third = api_client.post("/api/ingest/runs", json={"input": "c"}, headers=headers)

    assert third.status_code == 429


def test_the_rate_limit_refuses_a_client_in_a_loop(api_client, connected, monkeypatch):
    headers, _ = connected
    monkeypatch.setattr(ingest_service, "RATE_LIMIT_PER_MINUTE", 3)

    codes = [api_client.post("/api/ingest/runs", json={"input": "x"}, headers=headers).status_code
             for _ in range(5)]

    assert codes.count(429) >= 1, f"no request was throttled: {codes}"


# ── the reported topology ───────────────────────────────────────────────────

def test_a_connection_can_report_its_own_shape(api_client, connected):
    """In this direction nothing can go and ask, so the client has to tell."""
    headers, _ = connected

    resp = api_client.post("/api/ingest/topology", json={
        "framework": "langgraph",
        "nodes": [{"id": "triage"}, {"id": "answer"}, {"id": "ghost-target"}],
        "edges": [{"source": "triage", "target": "answer", "conditional": True},
                  {"source": "triage", "target": "missing"}],
    }, headers=headers)

    assert resp.status_code == 200
    stored = connection_store.get_connection("billing-graph")["topology"]
    assert [n["id"] for n in stored["nodes"]] == ["triage", "answer", "ghost-target"]
    # Bounded and validated by the same function that guards a pulled topology.
    assert [(e["source"], e["target"]) for e in stored["edges"]] == [("triage", "answer")]


def test_whoami_states_the_limits_so_a_client_can_size_its_batches(api_client, connected):
    headers, _ = connected

    body = api_client.get("/api/ingest/self", headers=headers).json()

    assert body["connection"]["id"] == "billing-graph"
    assert body["limits"]["events_per_request"] == ingest_service.MAX_EVENTS_PER_REQUEST
    assert "node_start" in body["frames"]


# ── the shared translation ──────────────────────────────────────────────────

def test_both_directions_translate_a_frame_the_same_way():
    """The pull path and the push path must not drift into rendering differently."""
    frames = [
        {"type": "node_start", "node": "triage"},
        {"type": "tool_start", "name": "search", "input": "q"},
        {"type": "tool_end", "name": "search", "output": "r"},
        {"type": "node_end", "node": "triage", "next": "answer"},
    ]

    translator = FrameTranslator()
    pushed = [e for f in frames for e in translator.feed(f)]

    from agents.remote_agent import _StreamState
    state = _StreamState(None, [])
    pulled = []
    state._emitter = pulled.append
    for frame in frames:
        state.handle(frame)

    assert [e["type"] for e in pushed] == [e["type"] for e in pulled]
    assert pushed == pulled


def test_the_connection_list_reports_what_has_been_running(api_client, connected):
    headers, _ = connected
    run_id = api_client.post("/api/ingest/runs", json={"input": "go"}, headers=headers).json()["run_id"]
    api_client.post(f"/api/ingest/runs/{run_id}/close", json={"ok": False, "error": "boom"},
                    headers=headers)

    listed = api_client.get("/api/connections").json()["connections"][0]

    assert listed["stats"]["runs"] == 1
    assert listed["stats"]["failed"] == 1
    assert listed["last_seen"], "a connection that has reported is not shown as never seen"


# ── retention ───────────────────────────────────────────────────────────────
#
# A production graph reports runs at a rate nothing else in this product does.
# The cap that keeps that bounded is also the one thing here that deletes data,
# so most of what follows is about what it must refuse to delete.

from connections import retention  # noqa: E402


def _report(api_client, headers, *, close=True, ok=True):
    """One reported run, optionally left open."""
    run_id = api_client.post("/api/ingest/runs", json={"input": "go"}, headers=headers).json()["run_id"]
    if close:
        api_client.post(f"/api/ingest/runs/{run_id}/close",
                        json={"ok": ok, "output": "done"}, headers=headers)
    return run_id


def test_a_connection_keeps_its_newest_runs_and_drops_the_rest(api_client, connected):
    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 3})
    reported = [_report(api_client, headers) for _ in range(6)]

    result = api_client.post("/api/connections/billing-graph/prune").json()

    assert result["removed_runs"] == 3
    kept = set(run_manager.get_runs_by_ids(reported))
    assert kept == set(reported[-3:]), "the newest three should be what is left"


def test_retention_never_deletes_a_run_that_is_still_reporting(api_client, connected):
    """A run in flight is work, whatever its age or position past the cap."""
    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 1})
    live = _report(api_client, headers, close=False)
    for _ in range(4):
        _report(api_client, headers)

    api_client.post("/api/connections/billing-graph/prune")

    assert live in run_manager.get_runs_by_ids([live]), "an open run was pruned"


def test_retention_never_touches_another_agents_runs(api_client, connected):
    """The prune is scoped by agent_id, which is also the connection's id."""
    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 0})
    run_manager.open_run("local-run-1", agent_id="swe_agent", session_type="chat")
    run_manager.close_run("local-run-1", status="completed", exit_code=0)
    for _ in range(3):
        _report(api_client, headers)

    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 1})
    api_client.post("/api/connections/billing-graph/prune")

    assert "local-run-1" in run_manager.get_runs_by_ids(["local-run-1"])


def test_the_payload_goes_with_the_run(api_client, connected):
    """A pruned run that left its payload behind would free nothing."""
    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 0})
    old_run = _report(api_client, headers)
    assert run_manager.get_run_process(old_run)

    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 0})
    _report(api_client, headers)
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 1})
    api_client.post("/api/connections/billing-graph/prune")

    from common import db
    row = db.get_conn().execute(
        "SELECT run_id FROM run_payloads WHERE run_id = ?", (old_run,)).fetchone()
    assert row is None


def test_pruning_clears_the_sessions_its_runs_left_behind(api_client, connected):
    """Each reported run without a thread is its own session; without this the
    Sessions page fills with empty conversations nothing points at."""
    from common import db

    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 1})
    for _ in range(4):
        _report(api_client, headers)

    api_client.post("/api/connections/billing-graph/prune")

    left = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE conversation_id LIKE 'conn:billing-graph:%'"
    ).fetchone()["n"]
    assert left == 1


def test_pruning_leaves_an_ordinary_empty_chat_alone(api_client, connected):
    """A conversation someone just opened has no runs either. Sweeping by
    "sessions with no runs" would delete it; the connection's prune is scoped to
    the conversation ids it writes."""
    from common import db
    from common.session_service import get_or_create_chat_session

    headers, _ = connected
    fresh_chat = get_or_create_chat_session("conv-just-opened", "New chat")
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 1})
    for _ in range(3):
        _report(api_client, headers)

    api_client.post("/api/connections/billing-graph/prune")

    row = db.get_conn().execute(
        "SELECT session_id FROM sessions WHERE session_id = ?", (fresh_chat,)).fetchone()
    assert row is not None


def test_zero_means_keep_everything(api_client, connected):
    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 0})
    reported = [_report(api_client, headers) for _ in range(5)]

    result = api_client.post("/api/connections/billing-graph/prune").json()

    assert result["removed_runs"] == 0
    assert len(run_manager.get_runs_by_ids(reported)) == 5


def test_a_connection_without_its_own_limit_uses_the_hub_default(monkeypatch):
    from common.config import settings

    monkeypatch.setattr(settings, "connection_retention_runs", 1500)

    assert retention.effective_limit({"id": "c", "retention_runs": None}) == 1500
    assert retention.effective_limit({"id": "c", "retention_runs": 10}) == 10
    # A stored value that is not a number must not make retention unbounded by
    # accident, which is the failure mode that quietly fills a disk.
    assert retention.effective_limit({"id": "c", "retention_runs": "lots"}) == 1500


def test_the_api_says_where_the_limit_comes_from(api_client, connected, monkeypatch):
    """A limit nobody can see is a limit people discover by missing data."""
    from common.config import settings

    monkeypatch.setattr(settings, "connection_retention_runs", 2000)

    default = api_client.get("/api/connections/billing-graph").json()["connection"]["retention"]
    assert default == {"runs": 2000, "source": "default"}

    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 50})
    own = api_client.get("/api/connections/billing-graph").json()["connection"]["retention"]
    assert own == {"runs": 50, "source": "connection"}


def test_the_daily_maintenance_pass_trims_connections(api_client, connected):
    """Retention has to happen without anyone pressing anything."""
    from common.maintenance import run_maintenance

    headers, _ = connected
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 2})
    reported = [_report(api_client, headers) for _ in range(5)]

    summary = run_maintenance(force=True)

    assert summary.get("pruned_connection_runs") == 3
    assert len(run_manager.get_runs_by_ids(reported)) == 2


# ── pausing for a human ─────────────────────────────────────────────────────
#
# A graph that stops to ask something (LangGraph's interrupt) parks its run
# here. The hub cannot push the answer back — in this direction nothing here
# ever calls the agent — so the client comes and collects it.


def _park(api_client, headers, question="Ship the release?"):
    run_id = api_client.post("/api/ingest/runs", json={"input": "release"}, headers=headers).json()["run_id"]
    api_client.post(f"/api/ingest/runs/{run_id}/interrupt", headers=headers, json={
        "question": question, "choices": ["yes", "no"], "key": "i-1", "node": "approve",
    })
    return run_id


def test_a_parked_run_keeps_its_question_where_it_can_be_found(api_client, connected):
    """A question that lives only in a stream nobody watched is a run that
    waits forever."""
    headers, _ = connected

    run_id = _park(api_client, headers)

    record = run_manager.get_run_by_id(run_id)
    assert record["status"] == "awaiting_input"
    assert record["pending_question"]["question"] == "Ship the release?"
    assert record["pending_question"]["choices"] == ["yes", "no"]
    assert record["pending_question"]["node"] == "approve"


def test_the_client_polls_and_gets_the_answer_once(api_client, connected):
    headers, _ = connected
    run_id = _park(api_client, headers)

    waiting = api_client.get(f"/api/ingest/runs/{run_id}/answer", headers=headers).json()
    assert waiting["status"] == "waiting"
    assert waiting["question"]["question"] == "Ship the release?"

    api_client.post(f"/api/connections/billing-graph/runs/{run_id}/answer",
                    json={"value": "yes", "answered_by": "anton"})

    answered = api_client.get(f"/api/ingest/runs/{run_id}/answer", headers=headers).json()
    assert answered == {"run_id": run_id, "status": "answered", "value": "yes",
                        "answered_at": answered["answered_at"]}

    # Collecting the answer is what finishes the parked run: asked, answered,
    # delivered. A second poll must not read as a fresh answer to act on again.
    again = api_client.get(f"/api/ingest/runs/{run_id}/answer", headers=headers).json()
    assert again["status"] == "completed"
    assert run_manager.get_run_by_id(run_id)["status"] == "completed"


def test_answering_a_run_that_is_not_waiting_is_refused(api_client, connected):
    headers, _ = connected
    run_id = _report(api_client, headers)

    resp = api_client.post(f"/api/connections/billing-graph/runs/{run_id}/answer",
                           json={"value": "yes"})

    assert resp.status_code == 409


def test_one_connection_cannot_read_another_connections_answer(api_client, connected):
    headers, _ = connected
    run_id = _park(api_client, headers)
    other = api_client.post("/api/connections", json={"id": "other", "name": "Other"}).json()["token"]

    resp = api_client.get(f"/api/ingest/runs/{run_id}/answer",
                          headers={"Authorization": f"Bearer {other}"})

    assert resp.status_code == 404


def test_a_parked_run_frees_its_slot_while_it_waits(api_client, connected, monkeypatch):
    """A person may take days to answer, and the connection has to keep working
    meanwhile."""
    headers, _ = connected
    monkeypatch.setattr(ingest_service, "MAX_OPEN_RUNS", 1)

    _park(api_client, headers)
    nxt = api_client.post("/api/ingest/runs", json={"input": "next"}, headers=headers)

    assert nxt.status_code == 201


def test_retention_never_deletes_a_run_that_is_waiting_for_a_person(api_client, connected):
    headers, _ = connected
    waiting = _park(api_client, headers)
    for _ in range(4):
        _report(api_client, headers)
    api_client.patch("/api/connections/billing-graph", json={"retention_runs": 1})

    api_client.post("/api/connections/billing-graph/prune")

    assert run_manager.get_run_by_id(waiting) is not None


def test_a_resumed_run_joins_the_run_it_continues(api_client, connected):
    """The pause is a person's lunch break, not a second piece of work."""
    headers, _ = connected
    parked = _park(api_client, headers)
    api_client.post(f"/api/connections/billing-graph/runs/{parked}/answer", json={"value": "yes"})
    api_client.get(f"/api/ingest/runs/{parked}/answer", headers=headers)

    resumed = api_client.post("/api/ingest/runs", headers=headers,
                              json={"input": "resume", "resumed_from": parked}).json()

    assert resumed["session_id"] == run_manager.get_run_by_id(parked)["session_id"]
    assert run_manager.get_run_by_id(resumed["run_id"])["resumed_from"] == parked


# ── the workspace boundary ──────────────────────────────────────────────────
#
# The only boundary this product has. There are no user accounts, so a
# connection cannot belong to a person or a team; it belongs to a workspace, and
# that is what one team's page shows and another's does not. What follows tests
# exactly that, and the docs say plainly where it stops.


def _connection_in(api_client, connection_id, workspace):
    return api_client.post("/api/connections", json={
        "id": connection_id, "name": connection_id, "workspace": workspace,
    }).json()


def test_a_connection_belongs_to_the_workspace_it_was_made_in(api_client):
    _connection_in(api_client, "team-a-graph", "team-a")
    _connection_in(api_client, "team-b-graph", "team-b")

    a = api_client.get("/api/connections", params={"workspace": "team-a"}).json()["connections"]
    b = api_client.get("/api/connections", params={"workspace": "team-b"}).json()["connections"]

    assert [c["id"] for c in a] == ["team-a-graph"]
    assert [c["id"] for c in b] == ["team-b-graph"]


def test_a_connection_made_without_a_workspace_lives_in_default(api_client):
    """It belongs where it was attached, rather than following the reader."""
    _connection_in(api_client, "unplaced", None)

    in_default = api_client.get("/api/connections", params={"workspace": "default"}).json()
    in_team_a = api_client.get("/api/connections", params={"workspace": "team-a"}).json()

    assert "unplaced" in [c["id"] for c in in_default["connections"]]
    assert "unplaced" not in [c["id"] for c in in_team_a["connections"]]

    # And the same rule on the single-connection endpoints, not only the list.
    assert api_client.get("/api/connections/unplaced",
                          params={"workspace": "default"}).status_code == 200
    assert api_client.get("/api/connections/unplaced",
                          params={"workspace": "team-a"}).status_code == 404


def test_a_workspace_sees_its_own_connections_and_not_defaults(api_client):
    _connection_in(api_client, "unplaced", None)
    _connection_in(api_client, "team-a-graph", "team-a")

    listed = api_client.get("/api/connections", params={"workspace": "team-a"}).json()

    assert [c["id"] for c in listed["connections"]] == ["team-a-graph"]


def test_another_workspace_reads_as_not_found_rather_than_forbidden(api_client):
    """Which it is, is not the other team's business."""
    _connection_in(api_client, "team-a-graph", "team-a")

    for method, path in (
        ("get", "/api/connections/team-a-graph"),
        ("post", "/api/connections/team-a-graph/rotate"),
        ("post", "/api/connections/team-a-graph/prune"),
        ("delete", "/api/connections/team-a-graph"),
    ):
        resp = getattr(api_client, method)(path, params={"workspace": "team-b"})
        assert resp.status_code == 404, f"{method.upper()} {path} leaked across workspaces"

    patched = api_client.patch("/api/connections/team-a-graph",
                               params={"workspace": "team-b"}, json={"name": "mine now"})
    assert patched.status_code == 404


def test_the_hub_wide_view_still_sees_everything(api_client):
    """No ``?workspace=`` means every workspace, which is what the API without a
    page behind it wants."""
    _connection_in(api_client, "team-a-graph", "team-a")
    _connection_in(api_client, "unplaced", None)

    assert api_client.get("/api/connections/team-a-graph").status_code == 200
    assert len(api_client.get("/api/connections").json()["connections"]) == 2


def test_a_connection_that_has_reported_cannot_change_workspace(api_client, connected):
    """Moving it would move the history: runs recorded for one team would start
    answering another team's queries, and costs already attributed would change
    hands."""
    headers, _ = connected
    _report(api_client, headers)

    resp = api_client.patch("/api/connections/billing-graph", json={"workspace": "team-b"})

    assert resp.status_code == 409
    assert "already reported" in resp.json()["detail"]
    assert connection_store.get_connection("billing-graph")["workspace"] is None


def test_a_connection_that_has_not_reported_can_still_be_placed(api_client):
    _connection_in(api_client, "fresh", None)

    resp = api_client.patch("/api/connections/fresh", json={"workspace": "team-a"})

    assert resp.status_code == 200
    assert resp.json()["connection"]["workspace"] == "team-a"


def test_reported_runs_land_in_the_connections_workspace_not_one_it_asks_for(api_client):
    """The client does not get a say: the workspace is a property of the
    connection the operator created, not of the request."""
    created = _connection_in(api_client, "team-a-graph", "team-a")
    headers = {"Authorization": f"Bearer {created['token']}"}

    run_id = api_client.post("/api/ingest/runs", headers=headers, json={
        "input": "go", "workspace": "team-b", "metadata": {"workspace": "team-b"},
    }).json()["run_id"]

    assert run_manager.get_run_by_id(run_id)["workspace"] == "team-a"
