"""
The one run store behind flow, loop and team runs (common/entity_runs.py).

Exercised over the three real tables, through stores built here with a
recording notify hook, so the tests see exactly which ``<resource>.changed``
events a write publishes. The adapters (flow/run_store.py, loops/store.py,
teams/store.py) have their own tests; these pin the shared semantics.
"""
from __future__ import annotations

import pytest

import common.db as db
from common.db_migrate import FLOW_RUN_COLUMNS
from common.entity_runs import EntityRunStore


class _Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, resource, **meta):
        self.events.append((resource, meta))


@pytest.fixture
def seen():
    return _Recorder()


@pytest.fixture
def flows(seen):
    """Whole-record mode: the ``doc`` column is the record."""
    return EntityRunStore(
        table="flow_runs", key="flow_run_id", columns=FLOW_RUN_COLUMNS,
        doc_column="doc", resource="flow_runs", parent_key="flow_id",
        order_by="COALESCE(started_at, ''), flow_run_id",
        live_statuses=("running", "pending"), notify=seen,
    )


_LOOP_COLUMNS = (
    "loop_run_id", "loop_id", "workspace", "status", "goal", "task_id",
    "session_id", "iterations_done", "best_score", "final_score",
    "stop_reason", "result", "error", "total_cost", "started_at", "finished_at",
)


@pytest.fixture
def loops(seen):
    """Sub-document mode: ``progress`` is exposed as the record's ``position``."""
    return EntityRunStore(
        table="loop_runs", key="loop_run_id", columns=_LOOP_COLUMNS,
        doc_column="progress", doc_field="position", resource="loop_runs",
        parent_key="loop_id", order_by="started_at DESC",
        live_statuses=("running", "stopping"), stopping_status="stopping",
        notify=seen,
    )


@pytest.fixture
def teams(seen):
    """Column-only mode."""
    return EntityRunStore(
        table="team_runs", key="team_run_id",
        columns=("team_run_id", "team_id", "status", "rounds_done", "started_at"),
        resource="team_runs", parent_key="team_id", order_by="started_at DESC",
        live_statuses=("running", "stopping"), stopping_status="stopping",
        notify=seen,
    )


# ── Upsert and update ─────────────────────────────────────────────────────────

def test_upsert_merges_into_an_existing_record(flows):
    flows.upsert({"flow_run_id": "f1", "flow_id": "A", "workspace": "ws", "note": "x"})
    flows.upsert({"flow_run_id": "f1", "status": "running"})
    rec = flows.get("f1")
    assert rec["status"] == "running"
    assert rec["workspace"] == "ws"
    assert rec["note"] == "x"


def test_upsert_without_merge_replaces_the_columns(teams):
    teams.upsert({"team_run_id": "t1", "team_id": "T", "status": "running", "rounds_done": 3})
    teams.upsert({"team_run_id": "t1", "team_id": "T", "status": "completed"}, merge=False)
    rec = teams.get("t1")
    assert rec["status"] == "completed"
    assert rec["rounds_done"] is None


def test_upsert_without_a_key_is_ignored(flows, seen):
    assert flows.upsert({"flow_id": "A"}) is None
    assert flows.list() == []
    assert seen.events == []


def test_json_doc_keys_survive_a_column_update(flows):
    flows.upsert({"flow_run_id": "f2", "flow_id": "A", "status": "pending"})
    flows.update("f2", {"checkpoint": {"node": "review"}})
    merged = flows.update("f2", {"status": "running", "pid": 7})
    assert merged["checkpoint"] == {"node": "review"}
    rec = flows.get("f2")
    assert rec["checkpoint"] == {"node": "review"}
    assert rec["pid"] == 7
    row = db.get_conn().execute(
        "SELECT status, pid FROM flow_runs WHERE flow_run_id = 'f2'").fetchone()
    assert (row["status"], row["pid"]) == ("running", 7)


def test_the_sub_document_rides_beside_the_columns(loops):
    loops.upsert({"loop_run_id": "l1", "loop_id": "L", "status": "running",
                  "position": {"iterations_done": 2}})
    loops.update("l1", {"iterations_done": 3})
    rec = loops.get("l1")
    assert rec["iterations_done"] == 3
    assert rec["position"] == {"iterations_done": 2}
    loops.update("l1", {"position": {"iterations_done": 3, "heartbeat_at": "now"}})
    assert loops.read("l1")["position"]["heartbeat_at"] == "now"
    assert loops.read("l1")["iterations_done"] == 3


def test_update_of_a_missing_run_is_none_and_writes_nothing(teams, seen):
    assert teams.update("nope", {"status": "running"}) is None
    assert teams.get("nope") is None
    assert seen.events == []


def test_update_can_be_conditional_on_status(teams):
    teams.upsert({"team_run_id": "t2", "team_id": "T", "status": "completed"})
    assert teams.update("t2", {"rounds_done": 9}, only_if_status=("running",)) is None
    assert teams.get("t2")["rounds_done"] is None
    assert teams.set_status("t2", "failed", from_statuses=("completed",))
    assert teams.get("t2")["status"] == "failed"


# ── Listing ───────────────────────────────────────────────────────────────────

def test_list_orders_filters_and_limits(teams):
    for i, (team, started) in enumerate([("A", "2026-01-01"), ("B", "2026-01-03"),
                                         ("A", "2026-01-02"), ("A", "2026-01-04")]):
        teams.upsert({"team_run_id": f"t{i}", "team_id": team, "status": "completed",
                      "started_at": started})
    assert [r["team_run_id"] for r in teams.list()] == ["t3", "t1", "t2", "t0"]
    assert [r["team_run_id"] for r in teams.list({"team_id": "A"}, limit=2)] == ["t3", "t2"]
    assert [r["team_run_id"] for r in teams.list(order_by="started_at")][:1] == ["t0"]


def test_list_rejects_a_filter_on_an_unknown_column(teams):
    with pytest.raises(ValueError):
        teams.list({"nope; DROP TABLE team_runs": 1})


def test_active_returns_only_live_statuses(flows):
    flows.upsert({"flow_run_id": "a1", "flow_id": "A", "status": "pending"})
    flows.upsert({"flow_run_id": "a2", "flow_id": "A", "status": "running"})
    flows.upsert({"flow_run_id": "a3", "flow_id": "A", "status": "completed"})
    flows.upsert({"flow_run_id": "b1", "flow_id": "B", "status": "running"})
    assert [r["flow_run_id"] for r in flows.active({"flow_id": "A"})] == ["a1", "a2"]
    assert len(flows.active()) == 3


def test_convert_shapes_what_callers_get(seen):
    store = EntityRunStore(
        table="team_runs", key="team_run_id", columns=("team_run_id", "status"),
        resource="team_runs", convert=lambda rec: (rec["team_run_id"], rec["status"]),
        notify=seen,
    )
    store.upsert({"team_run_id": "c1", "status": "running"})
    assert store.get("c1") == ("c1", "running")
    assert store.list() == [("c1", "running")]


# ── Stop requests ─────────────────────────────────────────────────────────────

def test_a_stop_request_round_trip(loops):
    loops.upsert({"loop_run_id": "s1", "loop_id": "L", "status": "running",
                  "position": {"heartbeat_at": "t"}})
    assert not loops.stop_requested("s1")
    assert loops.request_stop("s1") is True
    assert loops.stop_requested("s1")
    assert loops.get("s1")["status"] == "stopping"
    # The position is not lost by the status write.
    assert loops.get("s1")["position"] == {"heartbeat_at": "t"}
    # A second request finds nothing running to stop.
    assert loops.request_stop("s1") is False


def test_a_finished_or_unknown_run_is_not_stopped(teams):
    teams.upsert({"team_run_id": "s2", "team_id": "T", "status": "completed"})
    assert teams.request_stop("s2") is False
    assert teams.get("s2")["status"] == "completed"
    assert teams.request_stop("missing") is False
    assert teams.stop_requested("missing") is False


def test_a_store_without_a_stopping_status_refuses_a_stop_request(flows):
    with pytest.raises(NotImplementedError):
        flows.request_stop("x")


# ── Notification ──────────────────────────────────────────────────────────────

def test_writes_publish_the_resource_with_the_run_and_parent_ids(loops, seen):
    loops.upsert({"loop_run_id": "n1", "loop_id": "L", "status": "running"})
    loops.update("n1", {"iterations_done": 1})
    loops.request_stop("n1")
    loops.update("n1", {"position": {}}, notify=False)
    assert seen.events == [("loop_runs", {"loop_run_id": "n1", "loop_id": "L"})] * 3


def test_a_failing_notify_hook_does_not_fail_the_write(teams):
    def boom(resource, **meta):
        raise RuntimeError("broker down")
    store = EntityRunStore(table="team_runs", key="team_run_id",
                           columns=("team_run_id", "status"), resource="team_runs",
                           notify=boom)
    store.upsert({"team_run_id": "n2", "status": "running"})
    assert store.get("n2")["status"] == "running"


def test_the_default_hook_publishes_through_the_session_broker(monkeypatch):
    calls = []
    monkeypatch.setattr("common.session_broker.notify_change",
                        lambda resource, **meta: calls.append((resource, meta)))
    store = EntityRunStore(table="team_runs", key="team_run_id",
                           columns=("team_run_id", "team_id", "status"),
                           resource="team_runs", parent_key="team_id")
    store.upsert({"team_run_id": "n3", "team_id": "T", "status": "running"})
    assert calls == [("team_runs", {"team_run_id": "n3", "team_id": "T"})]


def test_the_adapters_keep_their_resource_names():
    from flow import run_store
    from loops import store as loop_store
    from teams import store as team_store
    assert run_store._RUNS.resource == "flow_runs"
    assert loop_store._RUNS.resource == "loop_runs"
    assert team_store._RUNS.resource == "team_runs"


# ── Construction ──────────────────────────────────────────────────────────────

def test_the_key_must_be_the_first_column():
    with pytest.raises(ValueError):
        EntityRunStore(table="team_runs", key="team_run_id",
                       columns=("status", "team_run_id"), resource="team_runs")
    with pytest.raises(ValueError):
        EntityRunStore(table="team_runs", key="team_run_id",
                       columns=("team_run_id",), resource="team_runs",
                       doc_field="position")
