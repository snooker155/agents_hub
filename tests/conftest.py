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

# Redirect the state root before common.paths is imported by anything else.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="agents_hub_tests_"))
os.environ["AGENTS_HUB_ROOT"] = str(_TEST_ROOT)

TEST_DATABASE_URL = os.environ.get("AGENTS_HUB_TEST_DATABASE_URL", "").strip()
os.environ["AGENTS_HUB_DATABASE_URL"] = TEST_DATABASE_URL

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


def _fresh_database(db, path: Path) -> None:
    """Point the process at an empty database: a new file on SQLite, the one
    shared database emptied on Postgres. Resets the per-thread connections and
    the schema-ready flag either way, so the next ``get_conn()`` runs the
    startup sequence (migrations, legacy JSON import) again."""
    db.DB_FILE = path
    db._local = threading.local()
    db._schema_ready = False
    if db.is_postgres():
        # A bare connection, not get_conn(): the startup sequence (migrations
        # and the legacy JSON import) must run only once the tables are empty
        # again, from the first get_conn() the test itself makes.
        conn = db._connect()
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ).fetchall()
        names = [str(r[0]) for r in rows if str(r[0]) != "schema_migrations"]
        if names:
            conn.execute("TRUNCATE " + ", ".join(names) + " RESTART IDENTITY")
        db._local = threading.local()
        db._schema_ready = False


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

    yield

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
