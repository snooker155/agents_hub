"""
Shared pytest fixtures.

Point the whole state root at a throwaway directory *before* any store module
imports, so the suite never reads or writes the real ``.agents_hub``. Each test
then gets a fresh SQLite database via the autouse ``fresh_db`` fixture.
"""
import os
import tempfile
import threading
from pathlib import Path

# Redirect the state root before common.paths is imported by anything else.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="agents_hub_tests_"))
os.environ["AGENTS_HUB_ROOT"] = str(_TEST_ROOT)

import pytest


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test an isolated, empty database.

    Points ``common.db.DB_FILE`` at a per-test file and resets the module's
    per-thread connection cache and schema-ready flag so the schema is rebuilt
    (and the empty-dir migration no-ops) against the fresh file.
    """
    import common.db as db

    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    db._local = threading.local()
    db._schema_ready = False

    # Neutralise change notifications: with no running event loop, notify_change
    # would otherwise spawn HTTP-relay timers. Patching the relay covers every
    # caller regardless of how it imported notify_change.
    import common.session_broker as sb
    monkeypatch.setattr(sb, "_relay_notify", lambda *a, **k: None)

    yield

    db._schema_ready = False
    db._local = threading.local()


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
