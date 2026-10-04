"""
Shared pytest fixtures.

Point the whole state root at a throwaway directory *before* any store module
imports, so the suite never reads or writes the real ``.agents_hub``. Each test
then gets a fresh, empty database via the autouse ``fresh_db`` fixture.

The suite runs against both database backends (docs/scaling.md):

- SQLite (default): every test gets its own file under ``tmp_path``.
- Postgres: set ``AGENTS_HUB_TEST_DATABASE_URL`` to a database the suite may
  empty, e.g. ``postgresql://ah:ah@localhost:5433/agents_hub``. Every test
  starts by truncating every table (the migration ledger excepted) in that
  one database, so it must not be shared with anything else. Tests marked
  ``sqlite_only`` (they open the SQLite file directly or test SQLite's own
  locking) are skipped.

``AGENTS_HUB_DATABASE_URL`` itself is pinned here to the test URL, or to the
empty string, so a developer's ``.env`` can never route the suite at a real
database.
"""
import os
import tempfile
import threading
from pathlib import Path
from typing import List

# Redirect the state root before common.paths is imported by anything else.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="agents_hub_tests_"))
os.environ["AGENTS_HUB_ROOT"] = str(_TEST_ROOT)

TEST_DATABASE_URL = os.environ.get("AGENTS_HUB_TEST_DATABASE_URL", "").strip()
os.environ["AGENTS_HUB_DATABASE_URL"] = TEST_DATABASE_URL

# Chat turns run in this process under test (chat/routing.py): the default
# hands every turn to a service replica, a process the suite must not spawn.
# Tests of the routing itself patch ``chat.routing.enabled``.
os.environ.setdefault("AGENTS_HUB_CHAT_EXECUTION", "inprocess")

# The backend's route modules import their request models as a top level
# ``models`` package (``from models import TaskCreate``), which only resolves
# with dashboard/backend on the path. Put it there before any test module is
# collected: a test file run on its own used to depend on an earlier file
# having done this, and a stray namespace package called ``models`` from
# another project installed in the same interpreter would otherwise be cached
# first and shadow it.
import sys as _sys

_BACKEND_DIR = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if _BACKEND_DIR not in _sys.path:
    _sys.path.insert(0, _BACKEND_DIR)

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "sqlite_only: the test opens the SQLite file directly; skipped on Postgres")


def pytest_collection_modifyitems(config, items):
    if not TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="SQLite-specific; the suite is running on Postgres")
    for item in items:
        if "sqlite_only" in item.keywords:
            item.add_marker(skip)


# The Postgres tables the suite empties before every test, listed once: the
# schema is built by the migrations on the first test's startup sequence and
# never changes afterwards, so asking information_schema again for each of the
# thousands of tests is pure overhead. Empty until the tables exist (the very
# first test on a new database finds none), and rebuilt once if a reset fails
# because the list went stale (a test that dropped or created a table itself).
_PG_TABLES: List[str] = []


def _pg_table_names(conn) -> List[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
    ).fetchall()
    return [str(r[0]) for r in rows if str(r[0]) != "schema_migrations"]


def _pg_reset_sql(tables: List[str]) -> str:
    """One round trip that empties every table and rewinds the sequences that
    were used. DELETE rather than TRUNCATE: nearly every table is empty before
    nearly every test, and a DELETE on an empty table costs nothing, whereas
    TRUNCATE takes an exclusive lock and allocates new storage for the table
    and each of its indexes, and then waits for the disk at commit; on seventy
    tables that was most of the Postgres run. setval(seq, 1, false) is what
    RESTART IDENTITY did, applied only to sequences that ever advanced
    (last_value is null until the first nextval). Sent as one multi-statement
    string, which Postgres runs as a single implicit transaction."""
    statements = [f"DELETE FROM {name}" for name in tables]
    statements.append(
        "SELECT setval(format('%I.%I', schemaname, sequencename)::regclass, 1, false) "
        "FROM pg_sequences WHERE schemaname = current_schema() AND last_value IS NOT NULL"
    )
    return "; ".join(statements)


def _fresh_database(db, path: Path) -> None:
    """Point the process at an empty database: a new file on SQLite, the one
    shared database emptied on Postgres. Resets the per-thread connections and
    the schema-ready flag either way, so the next ``get_conn()`` runs the
    startup sequence (migrations, legacy JSON import) again."""
    global _PG_TABLES
    db.DB_FILE = path
    db._local = threading.local()
    db._schema_ready = False
    if db.is_postgres():
        # A bare connection, not get_conn(): the startup sequence (migrations
        # and the legacy JSON import) must run only once the tables are empty
        # again, from the first get_conn() the test itself makes.
        conn = db._connect()
        if not _PG_TABLES:
            _PG_TABLES = _pg_table_names(conn)
        if _PG_TABLES:
            try:
                conn.execute(_pg_reset_sql(_PG_TABLES))
            except Exception:  # noqa: BLE001 - the cached table list went stale; rebuild it once
                _PG_TABLES = _pg_table_names(conn)
                if _PG_TABLES:
                    conn.execute(_pg_reset_sql(_PG_TABLES))
        db._local = threading.local()
        db._schema_ready = False


# Settings the developer's own .env may carry that the suite must not
# inherit: dashboard/backend/main.py exports the file into os.environ on
# import (load_dotenv), and common.config.live_setting reads the file first
# and the environment second, so without this a guard test would see the
# operator's "warn" instead of the default "block".
_PINNED_ENV = {
    "CAPABILITY_GUARD": ("capability_guard", "block"),
    "CAPABILITY_OVERRIDE_REQUIRES_CONTAINER": ("capability_override_requires_container", True),
}

# Keys a guard resolves live (common.config.agent_execution_mode and
# capability_guard._container_isolated) with the ``settings`` field as the
# fallback. Hidden from ``read_dot_env`` so a test that monkeypatches
# ``settings.agent_mode`` or ``settings.agent_docker_network`` is answered by
# its patch, not by the developer's AGENT_EXECUTION_MODE=docker, and dropped
# from os.environ even when the shell exported them before the session.
_SETTINGS_BACKED_ENV = frozenset({"AGENT_EXECUTION_MODE", "AGENT_DOCKER_NETWORK"})


# The environment as the session started, before any test imported
# dashboard/backend/main.py and its load_dotenv exported the developer's .env
# into os.environ. Keys the file adds on top of this are removed again before
# every test, so a live setting (common.config.live_setting) never answers
# from the operator's file in a test that monkeypatched ``settings``.
_BASE_ENV_KEYS = frozenset(os.environ)


def _dot_env_keys() -> set:
    try:
        from common.dotenv import read_env
        return set(read_env())
    except Exception:  # noqa: BLE001 - no readable .env, nothing to strip
        return set()


@pytest.fixture(autouse=True)
def guard_defaults(monkeypatch):
    """Keep the developer's .env out of the tests: every key the file exports
    into os.environ (load_dotenv on the backend import) is dropped again, the
    capability guard and execution mode keys are dropped from what
    ``read_dot_env`` returns, and the guard's ``settings`` fields (built from
    the same file at import) are reset to their defaults. A test that wants another mode sets it on
    ``settings`` or patches ``common.config.read_dot_env`` itself, as before."""
    import common.config as _cfg
    for key in _dot_env_keys() - _BASE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    real = _cfg.read_dot_env
    for key, (field, default) in _PINNED_ENV.items():
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(_cfg.settings, field, default, raising=False)
    hidden = _SETTINGS_BACKED_ENV | set(_PINNED_ENV)
    for key in _SETTINGS_BACKED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(_cfg, "read_dot_env", lambda: {k: v for k, v in real().items() if k not in hidden})
    # memory/rag_query.py reads the .env file through its own parser, not
    # through os.environ or common.config, so stripping the keys above does
    # not reach it: a developer's RAG_VECTOR_DB=chroma made every recall in
    # the suite import torch, fetch the embedding model and query the real
    # chroma_db (15 s per test). Its _read_env looks at os.environ first, so
    # a pinned "none" wins over the file; a test that wants RAG on sets both
    # variables itself, as the RAG tests already do.
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test an isolated, empty database.

    Points ``common.db.DB_FILE`` at a per-test file (SQLite) or empties the
    shared test database (Postgres), and resets the module's per-thread
    connection cache and schema-ready flag so the schema is rebuilt (and the
    empty-dir migration no-ops) against the fresh database.
    """
    import common.db as db

    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    _fresh_database(db, tmp_path / "test.db")

    # Neutralise change notifications: with no running event loop, notify_change
    # would otherwise spawn HTTP-relay timers. Patching the relay covers every
    # caller regardless of how it imported notify_change.
    import common.session_broker as sb
    monkeypatch.setattr(sb, "_relay_notify", lambda *a, **k: None)
    monkeypatch.setattr(sb, "_relay_publish", lambda *a, **k: None)

    yield

    # A heartbeat thread a test left behind (a run killed mid-flight) would
    # touch the database of a later test and, worse, run the schema setup on
    # its own connection and mark the schema ready for a file that has none.
    # Stop every one before the next test gets a fresh database.
    try:
        from runtime.entity_heartbeat import EntityHeartbeat
        for thread in threading.enumerate():
            if isinstance(thread, EntityHeartbeat):
                thread.stop()
    except Exception:  # noqa: BLE001 - a teardown guard, never a failure of its own
        pass
    # The same for the outbox drainer (notify/outbound.py): once a test starts
    # it, it polls the database for the rest of the session.
    # Only a live one: shutdown() joins the queue, which nothing would drain.
    outbound = _sys.modules.get("notify.outbound")
    worker = getattr(outbound, "_worker", None) if outbound is not None else None
    if worker is not None and worker.is_alive():
        outbound.shutdown()  # never raises

    db._schema_ready = False
    db._local = threading.local()


@pytest.fixture
def reopen_db(monkeypatch):
    """A callable that points the process at another empty database (a new
    file on SQLite, the truncated shared one on Postgres) and forces the next
    ``get_conn()`` through the startup sequence, for tests of that sequence."""
    import common.db as db

    def _reopen(path: Path) -> None:
        _fresh_database(db, path)

    return _reopen


@pytest.fixture
def no_launch(monkeypatch):
    """Stub agent_launcher.start_run so container/promotion logic never spawns a
    real subprocess. Returns the list of (task_id, agent_id) launch calls made."""
    calls = []

    def _fake_start_run(task_id, agent_id, params=None, run_id=None):
        calls.append((str(task_id), agent_id))
        return (run_id or "fake-run-id", "fake-session-id")

    import agents.agent_launcher as launcher
    monkeypatch.setattr(launcher, "start_run", _fake_start_run)
    return calls


@pytest.fixture(autouse=True)
def _reset_workspace_context():
    """Every test starts outside any workspace. A run started in process sets
    the workspace on a context var its tools scope by
    (common/workspace_scope.py); a test that left it set would put the next
    test "in" that workspace."""
    from common.workspace_context import _project_ctx, _workspace_ctx
    ws_token, project_token = _workspace_ctx.set(None), _project_ctx.set(None)
    yield
    _workspace_ctx.reset(ws_token)
    _project_ctx.reset(project_token)
