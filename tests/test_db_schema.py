"""Schema startup correctness: the ALTER-TABLE race and schema_version.

Audit defect 6: ``common.db._ensure_ready`` used to run ``_ensure_columns``
(post-hoc ``ALTER TABLE ADD COLUMN``) and ``executescript(_SCHEMA)`` outside
any transaction, although the module docstring promised an exclusive
transaction. Two processes opening the same pre-migration database at once
could both see a column missing (via ``PRAGMA table_info``) and both try to
ALTER it in; the loser got ``sqlite3.OperationalError: duplicate column
name``. Verified against the pre-fix code (see the PR/commit description) by
running this same multi-process race with an injected delay that widens the
real (very narrow) check-then-act window: 3 of 4 processes reliably raised
``duplicate column name: instance_id``.

The fix wraps the column ALTERs, schema creation, the new ``schema_version``
bump and the legacy-JSON import in one ``BEGIN IMMEDIATE`` transaction, so
concurrent processes serialize on SQLite's own write lock instead of racing
in Python.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import threading

import pytest

import common.db as db


# ── schema_version ───────────────────────────────────────────────────────────

def test_fresh_database_is_stamped_with_the_current_schema_version():
    conn = db.get_conn()
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert int(row["value"]) == db.SCHEMA_VERSION


def test_a_newer_schema_version_refuses_to_open(tmp_path, monkeypatch):
    """A state directory written by a newer build must fail loudly, not
    silently corrupt itself against migrations it doesn't recognise."""
    db_file = tmp_path / "future.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(db.SCHEMA_VERSION + 1),),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_FILE", db_file)
    db._local = threading.local()
    db._schema_ready = False
    try:
        with pytest.raises(RuntimeError, match="newer"):
            db.get_conn()
    finally:
        db._schema_ready = False
        db._local = threading.local()


def test_an_existing_database_without_the_key_is_upgraded_to_current():
    """A pre-existing db (no schema_version row at all) is the common case
    for every real installation upgrading into this change; it must not be
    mistaken for "newer" and must end up stamped, not left at 0."""
    conn = db.get_conn()
    conn.execute("DELETE FROM meta WHERE key='schema_version'")
    db._schema_ready = False  # force _ensure_ready to run again this test

    conn2 = db.get_conn()
    row = conn2.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert int(row["value"]) == db.SCHEMA_VERSION


# ── the ALTER-TABLE race ─────────────────────────────────────────────────────

def _prepare_pre_migration_db(root: str) -> None:
    """Write a fresh db file whose ``runs`` table predates ``instance_id`` /
    ``cached_prompt_tokens`` — the exact shape that triggers _ensure_columns's
    ALTER path, with no ``meta`` row at all (a real pre-upgrade installation)."""
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(os.path.join(root, "agents_hub.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, task_id TEXT, agent_id TEXT, session_id TEXT,
            session_type TEXT, channel TEXT, execution_mode TEXT, node_id TEXT,
            container_name TEXT, workspace TEXT, title TEXT, provider TEXT,
            model TEXT, status TEXT, message_origin TEXT, pid INTEGER,
            exit_code INTEGER, error TEXT, created_at TEXT, started_at TEXT,
            finished_at TEXT, log_file TEXT, input TEXT, output TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
            duration_ms INTEGER, extra TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _race_worker(root: str, q) -> None:
    """Run in a spawned subprocess: open the db exactly like a real
    entrypoint would (``common.db.get_conn``), against the shared
    pre-migration file at ``root``.

    Widens the real check-then-act window between ``PRAGMA table_info(runs)``
    and the ALTER that follows it by sleeping once, right after that read —
    this is what turns a rarely-hit interleaving into a deterministic one for
    the test, without changing the code under test. No barrier/rendezvous is
    needed: pre-fix, every process independently reads "column missing"
    before any of them has written it, so an unconditional per-process delay
    is enough to line them all up. Post-fix, only the single process holding
    the startup transaction's write lock ever reaches this statement at all
    (the others are blocked earlier, on ``BEGIN IMMEDIATE``), so the delay is
    dormant for them and cannot deadlock the fix's own serialization.
    """
    os.environ["AGENTS_HUB_ROOT"] = root

    class SlowConnection(sqlite3.Connection):
        _slept = False

        def execute(self, sql, *a, **k):
            cur = super().execute(sql, *a, **k)
            if not self._slept and isinstance(sql, str) and \
               sql.strip().upper().startswith("PRAGMA TABLE_INFO(RUNS)"):
                self._slept = True
                import time
                time.sleep(0.2)
            return cur

    _orig_connect = sqlite3.connect

    def _connect_slow(*a, **k):
        k.setdefault("factory", SlowConnection)
        return _orig_connect(*a, **k)

    sqlite3.connect = _connect_slow

    # Re-import fresh in this process (spawn start method: no inherited state).
    import common.db as db_mod
    try:
        conn = db_mod.get_conn()
        conn.execute("SELECT instance_id FROM runs LIMIT 1")
        q.put(("OK", None))
    except Exception as e:  # noqa: BLE001 - reporting the failure mode itself
        q.put(("FAIL", f"{type(e).__name__}: {e}"))


def test_four_processes_opening_a_stale_db_at_once_never_duplicate_a_column(tmp_path):
    """The regression test for audit defect 6.

    4 real OS processes (not threads: _schema_lock only ever protected
    same-process re-entrancy, never cross-process access) open the same
    pre-migration database at once. Before the fix this reliably produced
    ``sqlite3.OperationalError: duplicate column name`` in 3 of 4 processes;
    after the fix, the startup transaction serializes them and all 4 must
    succeed.
    """
    root = str(tmp_path / "race_root")
    _prepare_pre_migration_db(root)

    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_race_worker, args=(root, q)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=20)
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join()

    results = [q.get(timeout=5) for _ in range(4)]
    failures = [r for r in results if r[0] != "OK"]
    assert not failures, f"expected all 4 processes to succeed, got: {results}"
