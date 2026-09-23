"""
Flow-run records now live in SQLite, not in flow_runs.json.

What matters about that move is not where the bytes are: it is that a record is
still a free-form dict (callers checkpoint their own keys onto it and read them
back), that an existing installation's file is imported exactly once, and that
two processes writing different fields of the same run no longer overwrite each
other — which is precisely what the whole-file JSON rewrite did.
"""
from __future__ import annotations

import json
import threading


import common.db as db
import common.db_migrate as db_migrate
from flow import run_store


# ── Round trip ────────────────────────────────────────────────────────────────

def test_a_flow_run_survives_a_round_trip_through_the_table():
    run_store.open_flow_run(
        "fr-1", "flow-a", task_id="task-1", session_id="sess-1",
        workspace="ws", title="nightly", log_file="/tmp/x.log", pid=4242,
        status="pending",
    )
    rec = run_store.get_flow_run("fr-1")
    assert rec["flow_run_id"] == "fr-1"
    assert rec["flow_id"] == "flow-a"
    assert rec["task_id"] == "task-1"
    assert rec["workspace"] == "ws"
    assert rec["title"] == "nightly"
    assert rec["log_file"] == "/tmp/x.log"
    assert rec["pid"] == 4242
    assert rec["status"] == "pending"


def test_the_lifecycle_moves_the_record_through_its_states():
    run_store.open_flow_run("fr-2", "flow-a")
    run_store.mark_running("fr-2", pid=99)
    running = run_store.get_flow_run("fr-2")
    assert running["status"] == "running" and running["pid"] == 99
    assert running["started_at"]

    run_store.close_flow_run("fr-2", status="completed", exit_code=0)
    done = run_store.get_flow_run("fr-2")
    assert done["status"] == "completed"
    assert done["exit_code"] == 0
    assert done["finished_at"]


def test_load_flow_runs_returns_every_record_oldest_first():
    run_store.open_flow_run("fr-a", "flow-a")
    run_store.open_flow_run("fr-b", "flow-b")
    ids = [r["flow_run_id"] for r in run_store.load_flow_runs()]
    assert ids == ["fr-a", "fr-b"]


def test_a_missing_run_reads_as_none_and_updates_to_none():
    assert run_store.get_flow_run("nope") is None
    assert run_store.update_flow_run("nope", {"status": "running"}) is None


# ── Arbitrary keys ────────────────────────────────────────────────────────────

def test_keys_the_table_has_no_column_for_are_preserved():
    """Other parts of the system park a checkpoint on a flow run. A record is a
    document, so a key nobody declared has to come back unchanged."""
    run_store.open_flow_run("fr-3", "flow-a")
    checkpoint = {"node": "review", "attempt": 3, "state": {"items": [1, 2, 3]}}
    run_store.update_flow_run("fr-3", {"checkpoint": checkpoint, "resume_token": "abc"})

    rec = run_store.get_flow_run("fr-3")
    assert rec["checkpoint"] == checkpoint
    assert rec["resume_token"] == "abc"
    # And they survive the next write of an indexed field.
    run_store.mark_running("fr-3", pid=7)
    rec = run_store.get_flow_run("fr-3")
    assert rec["checkpoint"] == checkpoint
    assert rec["pid"] == 7


def test_an_upsert_merges_into_an_existing_record_rather_than_replacing_it():
    run_store.open_flow_run("fr-4", "flow-a", workspace="ws")
    run_store.update_flow_run("fr-4", {"checkpoint": {"node": "draft"}})
    run_store.upsert_flow_run({"flow_run_id": "fr-4", "status": "running"})

    rec = run_store.get_flow_run("fr-4")
    assert rec["status"] == "running"
    assert rec["workspace"] == "ws"
    assert rec["checkpoint"] == {"node": "draft"}


def test_the_indexed_columns_mirror_the_document():
    """The columns exist so queries can filter without unpacking JSON; a write
    that only touches the document would make them lie."""
    run_store.open_flow_run("fr-5", "flow-a", workspace="ws")
    run_store.update_flow_run("fr-5", {"status": "running", "checkpoint": {"n": 1}})
    row = db.get_conn().execute(
        "SELECT flow_id, status, workspace FROM flow_runs WHERE flow_run_id = 'fr-5'"
    ).fetchone()
    assert (row["flow_id"], row["status"], row["workspace"]) == ("flow-a", "running", "ws")


# ── Active runs ───────────────────────────────────────────────────────────────

def test_only_this_flows_unfinished_runs_count_as_active():
    run_store.open_flow_run("fr-6", "flow-a", status="pending")
    run_store.open_flow_run("fr-7", "flow-a")
    run_store.close_flow_run("fr-7", status="completed")
    run_store.open_flow_run("fr-8", "flow-b", status="pending")

    active = run_store.get_active_flow_runs("flow-a")
    assert [r["flow_run_id"] for r in active] == ["fr-6"]


def test_a_run_whose_process_is_gone_is_reconciled_instead_of_staying_active(monkeypatch):
    run_store.open_flow_run("fr-9", "flow-c")
    run_store.mark_running("fr-9", pid=123456)
    monkeypatch.setattr(run_store, "_pid_alive", lambda pid: False)

    assert run_store.get_active_flow_runs("flow-c") == []
    assert run_store.get_flow_run("fr-9")["status"] == "stopped"


# ── Concurrency ───────────────────────────────────────────────────────────────

def test_two_threads_checkpointing_different_fields_keep_both():
    """The defect this store replaced: each writer rewrote the whole file from
    a snapshot it had read, so the slower one silently dropped the other's
    field. The merge now happens inside one transaction."""
    run_store.open_flow_run("fr-10", "flow-a")
    start = threading.Barrier(2)
    errors = []

    def write(key, value):
        try:
            start.wait(timeout=5)
            for _ in range(20):
                run_store.update_flow_run("fr-10", {key: value})
        except Exception as exc:  # pragma: no cover - only on a real failure
            errors.append(exc)

    threads = [threading.Thread(target=write, args=("from_a", 1)),
               threading.Thread(target=write, args=("from_b", 2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    rec = run_store.get_flow_run("fr-10")
    assert rec["from_a"] == 1
    assert rec["from_b"] == 2


# ── Migration ─────────────────────────────────────────────────────────────────

def _reopen(monkeypatch, tmp_path, state_root, reopen_db):
    """Point the database and the migration source at a fresh directory and
    force the next get_conn() to run the startup sequence again."""
    monkeypatch.setattr(db_migrate, "AGENTS_HUB_ROOT", state_root)
    reopen_db(tmp_path / "migrate.db")


def test_an_existing_flow_runs_file_is_imported_and_renamed(monkeypatch, tmp_path, reopen_db):
    state_root = tmp_path / "state"
    state_root.mkdir()
    src = state_root / "flow_runs.json"
    src.write_text(json.dumps([
        {"flow_run_id": "old-1", "flow_id": "flow-a", "status": "completed",
         "workspace": "ws", "exit_code": 0, "checkpoint": {"node": "review"}},
        {"flow_run_id": "old-2", "flow_id": "flow-b", "status": "running", "pid": 5},
        {"not_a_run": True},
    ]), encoding="utf-8")

    _reopen(monkeypatch, tmp_path, state_root, reopen_db)
    try:
        db.get_conn()

        assert run_store.get_flow_run("old-1")["checkpoint"] == {"node": "review"}
        assert run_store.get_flow_run("old-2")["pid"] == 5
        assert len(run_store.load_flow_runs()) == 2

        # The source is renamed so a half-upgraded process cannot keep writing
        # to a file nothing reads any more.
        assert not src.exists()
        assert (state_root / "flow_runs.json.migrated").exists()
    finally:
        db._schema_ready = False
        db._local = threading.local()


def test_the_import_runs_once_even_after_the_records_are_edited(monkeypatch, tmp_path, reopen_db):
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "flow_runs.json").write_text(
        json.dumps([{"flow_run_id": "old-1", "flow_id": "flow-a", "status": "running"}]),
        encoding="utf-8")

    _reopen(monkeypatch, tmp_path, state_root, reopen_db)
    try:
        db.get_conn()
        run_store.close_flow_run("old-1", status="completed")

        # Put the file back and reopen: the marker, not the file's absence, is
        # what stops a second import from resurrecting the old status.
        (state_root / "flow_runs.json").write_text(
            json.dumps([{"flow_run_id": "old-1", "flow_id": "flow-a", "status": "running"}]),
            encoding="utf-8")
        db._schema_ready = False
        db._local = threading.local()
        db.get_conn()

        assert run_store.get_flow_run("old-1")["status"] == "completed"
    finally:
        db._schema_ready = False
        db._local = threading.local()


def test_a_fresh_database_is_stamped_with_the_schema_version_that_added_flow_runs():
    row = db.get_conn().execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row["value"]) == db.SCHEMA_VERSION
    assert db.SCHEMA_VERSION >= 2
