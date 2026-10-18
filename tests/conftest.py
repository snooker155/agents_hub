"""
Shared pytest fixtures.

Point the whole state root at a throwaway directory *before* any store module
imports, so the suite never reads or writes the real ``.agents_hub``. Each test
then gets a fresh, empty SQLite file under ``tmp_path`` via the autouse
``fresh_db`` fixture.
"""
import logging
import os
import tempfile
import threading
from pathlib import Path

# Redirect the state root before common.paths is imported by anything else.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="agents_hub_tests_"))
os.environ["AGENTS_HUB_ROOT"] = str(_TEST_ROOT)


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
    # Kept so the tests that carried the marker still collect: the suite runs
    # on SQLite only, so the marker no longer skips anything.
    config.addinivalue_line("markers", "sqlite_only: the test opens the SQLite file directly")


def _fresh_database(db, path: Path) -> None:
    """Point the process at a new, empty SQLite file. Resets the per-thread
    connections and the schema-ready flag, so the next ``get_conn()`` runs the
    startup sequence (migrations, legacy JSON import) again."""
    db.DB_FILE = path
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
def provider_env_isolated(tmp_path, monkeypatch):
    """Keep the developer's model keys out of the tests, and a key a test
    saves out of the next one: common/provider_env.py reads a file of the
    test's own, and the provider variables and ``settings`` fields it copies
    into the process are put back afterwards."""
    from common import provider_env
    from common.config import settings
    monkeypatch.setattr(provider_env, "ENV_FILE", tmp_path / "provider.env")
    for field in provider_env._SETTINGS_FIELD.values():
        if hasattr(settings, field):
            monkeypatch.setattr(settings, field, getattr(settings, field), raising=False)
    saved = {key: os.environ.get(key) for key in provider_env.PROVIDER_ENV_KEYS}
    # The developer's own model keys reach nothing: a test must never make a
    # live call on one. A test that needs a key saves one.
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
        field = provider_env._SETTINGS_FIELD.get(key)
        if field and hasattr(settings, field):
            monkeypatch.setattr(settings, field, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test an isolated, empty database.

    Points ``common.db.DB_FILE`` at a per-test file and resets the module's
    per-thread connection cache and schema-ready flag so the schema is rebuilt
    (and the empty-dir migration no-ops) against the fresh database. The
    leases live in process memory (common/leases.py), so they are forgotten
    too: a lease one test took must not stop the next one's loop.
    """
    import common.db as db
    from common import leases

    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    _fresh_database(db, tmp_path / "test.db")
    leases.reset()
    leases.set_owner_id(None)

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
        logging.getLogger(__name__).debug("heartbeat teardown failed", exc_info=True)

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
    test "in" that workspace.

    The same goes for the environment: runtime/agent_run.py, flow_run.py and
    instance_run.py are subprocess entry points that export the run's identity
    into os.environ, and a test that calls their main() in process would leave
    AGENT_WORKSPACE behind for every later test on that worker."""
    from common.workspace_context import _project_ctx, _workspace_ctx
    ws_token, project_token = _workspace_ctx.set(None), _project_ctx.set(None)
    saved_env = {key: os.environ.pop(key, None) for key in _RUN_IDENTITY_ENV}
    yield
    _workspace_ctx.reset(ws_token)
    _project_ctx.reset(project_token)
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# What a runtime entry point exports about the run it is (see above).
_RUN_IDENTITY_ENV = ("AGENT_WORKSPACE", "AGENT_RUN_ID", "AGENT_INSTANCE_ID", "AGENT_LOG_FILE",
                     "AGENTS_HUB_ROLE")
