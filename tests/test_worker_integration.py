"""A real worker process on the real spawn path.

The unit tests stub the launch; this one runs ``python -m runtime.worker
--once`` as a subprocess against the test database and lets it spawn
``runtime/agent_run.py`` for real. The agent id does not exist, so the child
exits at its first check with "Unknown agent_id" and no model is ever
called, but everything the worker owns (claim, spawn, the run record's pid
and host, the log header, the queue row's lifecycle) is exercised with real
processes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from common import run_queue
from common.paths import PROJECT_ROOT
from managers import run_manager as rm


@pytest.fixture
def shared_root(tmp_path, reopen_db, monkeypatch):
    """A state root the subprocess and this test both open: on SQLite the
    database file must be the one the child computes from AGENTS_HUB_ROOT."""
    root = tmp_path / "root"
    root.mkdir()
    reopen_db(root / "agents_hub.db")
    monkeypatch.setenv("AGENTS_HUB_ROOT", str(root))
    return root


def _worker_once(root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AGENTS_HUB_ROOT"] = str(root)
    env["AGENTS_HUB_ROLE"] = "worker"
    env["AGENTS_HUB_INSTANCE_ID"] = "worker-under-test"
    return subprocess.run(
        [sys.executable, "-m", "runtime.worker", "--once", "--modes", "local"],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=180,
    )


def test_a_worker_process_claims_spawns_and_closes_a_launch(shared_root):
    from tasks import service as ts

    t = ts.create_task("real worker")
    run_id = "worker-int-1"
    log_file = shared_root / "run_logs" / f"agent_run_{run_id}.log"
    rm.preopen_run(run_id, "no_such_agent", task_id=str(t.id), status="queued",
                   log_file=str(log_file), link_to_session=False)
    run_queue.enqueue(run_id, "task", {
        "kind": "task", "run_id": run_id, "task_id": str(t.id), "agent_id": "no_such_agent",
        "session_id": "", "instance_id": None, "workspace": "default",
        "ws_path": str(shared_root), "log_file": str(log_file),
        "cli_args": ["no_such_agent", "--workspace", str(shared_root),
                     "--task-id", str(t.id), "--run-id", run_id],
        "execution_mode": "local",
    }, execution_mode="local")

    first = _worker_once(shared_root)
    assert first.returncode == 0, first.stderr
    assert "launched 1" in first.stdout

    from common import db
    db.reset_connections()
    rec = rm.get_run_by_id(run_id)
    assert rec["status"] == "running"
    assert rec["pid"] and rec["host"]
    row = run_queue.get(run_id)
    assert row["status"] == run_queue.STATUS_RUNNING
    assert row["lease_owner"] == "worker-under-test"

    # The child exits at once (unknown agent); the next tick reaps it.
    deadline = time.time() + 60
    while time.time() < deadline and rm._pid_exists(int(rec["pid"])):
        time.sleep(0.2)
    assert not rm._pid_exists(int(rec["pid"]))
    assert "Unknown agent_id" in log_file.read_text(encoding="utf-8")
    assert "Host:" in log_file.read_text(encoding="utf-8")

    # A second worker never relaunches a row another worker still leases.
    second = _worker_once(shared_root)
    assert second.returncode == 0, second.stderr
    assert "launched 0" in second.stdout
    db.reset_connections()
    assert run_queue.get(run_id)["status"] == run_queue.STATUS_RUNNING

    # The first worker is gone (a --once process exits), so its lease lapses
    # and the sweep closes the row as detached rather than launching again.
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE run_queue SET lease_until = ? WHERE run_id = ?", (past, run_id))
    assert run_queue.sweep() == {"closed": 1, "requeued": 0, "failed": 0}
    assert run_queue.get(run_id)["status"] == run_queue.STATUS_DONE
