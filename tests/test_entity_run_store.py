"""
The one run table behind flow, loop, team and scenario runs (common/entity_runs.py).

Exercised through per-kind stores built here with a recording notify hook,
so the tests see exactly which ``<resource>.changed`` events a write
publishes, and through the kind-agnostic functions the watchdog and the run
groups use. The adapters (flow/run_store.py, loops/store.py, teams/store.py,
playground/store.py) have their own tests; these pin the shared semantics:
one table, columns laid over the document, the checkpoint apart, the
transition table enforced, both spellings of the ids on every record.
"""
from __future__ import annotations

import pytest

import common.db as db
from common import entity_runs
from common.entity_runs import EntityRunStore
from common.run_status import IllegalRunTransition, RunStatus


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
    return EntityRunStore("flow", order_by="COALESCE(started_at, ''), run_id",
                          live_statuses=("running", "pending"), notify=seen)


@pytest.fixture
def loops(seen):
    return EntityRunStore("loop", notify=seen)


@pytest.fixture
def teams(seen):
    return EntityRunStore("team", notify=seen)


# ── Upsert and update ─────────────────────────────────────────────────────────

def test_upsert_merges_into_an_existing_record(flows):
    flows.upsert({"flow_run_id": "f1", "flow_id": "A", "workspace": "ws", "note": "x"})
    flows.upsert({"flow_run_id": "f1", "status": "running"})
    rec = flows.get("f1")
    assert rec["status"] == "running"
    assert rec["workspace"] == "ws"
    assert rec["note"] == "x"


def test_a_record_carries_both_spellings_of_its_ids(teams):
    teams.upsert({"team_run_id": "t1", "team_id": "T", "status": "pending"})
    rec = teams.get("t1")
    assert rec["run_id"] == "t1" and rec["team_run_id"] == "t1"
    assert rec["entity_id"] == "T" and rec["team_id"] == "T"
    assert rec["kind"] == "team"


def test_upsert_without_merge_replaces_the_document(teams):
    teams.upsert({"team_run_id": "t1", "team_id": "T", "status": "running", "goal": "x"})
    teams.upsert({"team_run_id": "t1", "team_id": "T", "status": "running"}, merge=False)
    assert "goal" not in teams.get("t1")


def test_upsert_without_a_key_is_ignored(flows, seen):
    assert flows.upsert({"flow_id": "A"}) is None
    assert flows.list() == []
    assert seen.events == []


def test_json_doc_keys_survive_a_column_update(flows):
    flows.upsert({"flow_run_id": "f2", "flow_id": "A", "status": "pending",
                  "resume_token": "abc"})
    flows.update("f2", {"status": "running", "pid": 7})
    rec = flows.get("f2")
    assert rec["resume_token"] == "abc"
    assert rec["pid"] == 7
    row = db.get_conn().execute(
        "SELECT status, pid, kind FROM entity_runs WHERE run_id = 'f2'").fetchone()
    assert (row["status"], row["pid"], row["kind"]) == ("running", 7, "flow")


def test_the_checkpoint_has_a_column_of_its_own(flows):
    flows.upsert({"flow_run_id": "f3", "flow_id": "A", "status": "running"})
    assert "checkpoint" not in flows.get("f3")
    flows.update("f3", {"checkpoint": {"node": "review"}})
    assert flows.get("f3")["checkpoint"] == {"node": "review"}
    row = db.get_conn().execute(
        "SELECT checkpoint, doc FROM entity_runs WHERE run_id = 'f3'").fetchone()
    assert db.loads(row["checkpoint"]) == {"node": "review"}
    assert "checkpoint" not in db.loads(row["doc"])
    # The generic surface reads and writes the same column.
    entity_runs.save_checkpoint("f3", {"node": "publish"})
    assert entity_runs.load_checkpoint("f3") == {"node": "publish"}
    assert flows.get("f3")["checkpoint"] == {"node": "publish"}


def test_a_column_stamped_directly_wins_over_the_document(loops):
    loops.upsert({"loop_run_id": "l1", "loop_id": "L", "status": "running"})
    assert entity_runs.touch_heartbeat("l1", "2026-09-23T10:00:00+00:00") == "running"
    assert loops.get("l1")["heartbeat_at"] == "2026-09-23T10:00:00+00:00"
    assert entity_runs.touch_heartbeat("nope") is None


def test_update_of_a_missing_run_is_none_and_writes_nothing(teams, seen):
    assert teams.update("nope", {"status": "running"}) is None
    assert teams.get("nope") is None
    assert seen.events == []


def test_a_store_only_sees_its_own_kind(teams, loops):
    teams.upsert({"team_run_id": "x1", "team_id": "T", "status": "running"})
    assert loops.get("x1") is None
    assert loops.update("x1", {"goal": "g"}) is None
    assert entity_runs.get("x1")["kind"] == "team"


def test_update_can_be_conditional_on_status(teams):
    teams.upsert({"team_run_id": "t2", "team_id": "T", "status": "running"})
    assert teams.update("t2", {"rounds_done": 9}, only_if_status=("pending",)) is None
    assert teams.get("t2").get("rounds_done") is None
    assert teams.set_status("t2", "failed", from_statuses=("running",))
    assert teams.get("t2")["status"] == "failed"


# ── The transition table ──────────────────────────────────────────────────────

def test_a_forbidden_status_move_is_refused(teams):
    teams.upsert({"team_run_id": "t3", "team_id": "T", "status": "completed"})
    with pytest.raises(IllegalRunTransition):
        teams.update("t3", {"status": "running"})
    with pytest.raises(IllegalRunTransition):
        teams.upsert({"team_run_id": "t3", "team_id": "T", "status": "pending"})
    assert teams.get("t3")["status"] == "completed"


def test_a_resume_moves_a_stopped_or_failed_run_back_to_running(loops):
    loops.upsert({"loop_run_id": "l2", "loop_id": "L", "status": "stopped"})
    loops.update("l2", {"status": "running"})
    loops.update("l2", {"status": "failed"})
    loops.update("l2", {"status": "pending"})
    assert loops.get("l2")["status"] == "pending"


def test_a_same_status_write_is_always_allowed(teams):
    teams.upsert({"team_run_id": "t4", "team_id": "T", "status": "completed"})
    assert teams.update("t4", {"status": "completed", "result": "done"})["result"] == "done"


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
        teams.list({"nope; DROP TABLE entity_runs": 1})


def test_active_returns_only_live_statuses(flows):
    flows.upsert({"flow_run_id": "a1", "flow_id": "A", "status": "pending"})
    flows.upsert({"flow_run_id": "a2", "flow_id": "A", "status": "running"})
    flows.upsert({"flow_run_id": "a3", "flow_id": "A", "status": "completed"})
    flows.upsert({"flow_run_id": "b1", "flow_id": "B", "status": "running"})
    assert [r["flow_run_id"] for r in flows.active({"flow_id": "A"})] == ["a1", "a2"]
    assert len(flows.active()) == 3


def test_the_generic_listing_spans_kinds(flows, teams):
    flows.upsert({"flow_run_id": "g1", "flow_id": "A", "status": "running", "workspace": "w"})
    teams.upsert({"team_run_id": "g2", "team_id": "T", "status": "pending", "workspace": "w",
                  "task_id": "task-1"})
    teams.upsert({"team_run_id": "g3", "team_id": "T", "status": "completed", "workspace": "v"})
    kinds = {r["kind"] for r in entity_runs.list_runs(active=True)}
    assert kinds == {"flow", "team"}
    assert [r["run_id"] for r in entity_runs.list_runs(kind="team", workspace="v")] == ["g3"]
    assert [r["run_id"] for r in entity_runs.list_runs(task_id="task-1")] == ["g2"]
    assert entity_runs.counts_by_kind(statuses=("running", "pending")) == {"flow": 1, "team": 1}


def test_children_are_found_by_parent_run_id(flows, teams):
    flows.upsert({"flow_run_id": "p1", "flow_id": "A", "status": "running"})
    teams.upsert({"team_run_id": "c1", "team_id": "T", "status": "running",
                  "parent_run_id": "p1"})
    from managers import run_manager as rm
    rm.upsert_run({"run_id": "leaf-1", "agent_id": "a", "status": "running",
                   "parent_run_id": "p1"})
    assert [r["run_id"] for r in entity_runs.children("p1")] == ["c1"]
    assert entity_runs.leaf_children("p1") == ["leaf-1"]


def test_convert_shapes_what_callers_get(seen):
    store = EntityRunStore("team", convert=lambda rec: (rec["team_run_id"], rec["status"]),
                           notify=seen)
    store.upsert({"team_run_id": "c1", "team_id": "T", "status": "running"})
    assert store.get("c1") == ("c1", "running")
    assert store.list() == [("c1", "running")]


# ── Stop requests ─────────────────────────────────────────────────────────────

def test_a_stop_request_round_trip(loops):
    loops.upsert({"loop_run_id": "s1", "loop_id": "L", "status": "running"})
    assert not loops.stop_requested("s1")
    assert loops.request_stop("s1") is True
    assert loops.get("s1")["status"] == "stopping"
    assert loops.stop_requested("s1")
    # Asking twice changes nothing.
    assert loops.request_stop("s1") is False
    loops.update("s1", {"status": "stopped"})
    assert loops.stop_requested("s1")


def test_a_finished_or_unknown_run_is_not_stopped(teams):
    teams.upsert({"team_run_id": "s2", "team_id": "T", "status": "completed"})
    assert teams.request_stop("s2") is False
    assert teams.request_stop("nope") is False
    assert teams.get("s2")["status"] == "completed"


def test_a_pending_run_can_be_asked_to_stop(teams):
    teams.upsert({"team_run_id": "s3", "team_id": "T", "status": "pending"})
    assert entity_runs.request_stop("s3") is True
    assert teams.get("s3")["status"] == RunStatus.stopping.value


# ── Notification ──────────────────────────────────────────────────────────────

def test_writes_publish_the_resource_with_the_run_and_parent_ids(loops, seen):
    loops.upsert({"loop_run_id": "n1", "loop_id": "L", "status": "running"})
    assert seen.events[-1][0] == "loop_runs"
    assert seen.events[-1][1]["loop_run_id"] == "n1"
    assert seen.events[-1][1]["loop_id"] == "L"
    assert seen.events[-1][1]["status"] == "running"
    entity_runs.save_checkpoint("n1", {"iterations_done": 1})
    assert len(seen.events) == 1   # a checkpoint write is silent


def test_a_failing_notify_hook_does_not_fail_the_write():
    def _boom(resource, **meta):
        raise RuntimeError("no broker")
    store = EntityRunStore("team", notify=_boom)
    assert store.upsert({"team_run_id": "n2", "team_id": "T", "status": "running"})
    assert store.get("n2")["status"] == "running"


def test_the_default_hook_publishes_through_the_session_broker(monkeypatch):
    calls = []
    monkeypatch.setattr("common.session_broker.notify_change",
                        lambda resource, **meta: calls.append((resource, meta)))
    store = EntityRunStore("scenario")
    store.upsert({"sim_run_id": "n3", "scenario_id": "S", "status": "pending"})
    assert calls and calls[-1][0] == "sim_runs"
    assert calls[-1][1]["sim_run_id"] == "n3"


def test_the_adapters_keep_their_resource_names():
    from flow import run_store
    from loops import store as loop_store
    from playground import store as sim_store
    from teams import store as team_store
    assert run_store.RUNS.resource == "flow_runs"
    assert loop_store.RUNS.resource == "loop_runs"
    assert team_store.RUNS.resource == "team_runs"
    assert sim_store.RUNS.resource == "sim_runs"
    assert entity_runs.store_for("team") is team_store.RUNS


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        EntityRunStore("container")
    with pytest.raises(ValueError):
        entity_runs.upsert({"run_id": "x"}, kind="nope")
