"""Retention/maintenance tests: only old terminal runs are pruned."""
from datetime import datetime, timedelta, timezone

from managers import run_manager as rm
from common import maintenance


def _iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_prune_old_runs_only_removes_old_terminal():
    # Old + terminal → pruned.
    rm.upsert_run({"run_id": "old-done", "agent_id": "a", "status": "completed",
                   "finished_at": _iso_days_ago(40)})
    # Recent + terminal → kept.
    rm.upsert_run({"run_id": "new-done", "agent_id": "a", "status": "completed",
                   "finished_at": _iso_days_ago(1)})
    # Old but still running → kept (never delete active work).
    rm.upsert_run({"run_id": "old-running", "agent_id": "a", "status": "running",
                   "finished_at": None})
    # Old + failed → pruned.
    rm.upsert_run({"run_id": "old-failed", "agent_id": "a", "status": "failed",
                   "finished_at": _iso_days_ago(90)})

    removed = maintenance.prune_old_runs(retention_days=30)
    assert removed == 2
    assert rm.get_run_by_id("old-done") is None
    assert rm.get_run_by_id("old-failed") is None
    assert rm.get_run_by_id("new-done") is not None
    assert rm.get_run_by_id("old-running") is not None


def test_prune_old_runs_disabled_when_zero():
    rm.upsert_run({"run_id": "x", "agent_id": "a", "status": "completed",
                   "finished_at": _iso_days_ago(999)})
    assert maintenance.prune_old_runs(retention_days=0) == 0
    assert rm.get_run_by_id("x") is not None


def test_prune_old_runs_deletes_payload_row():
    rm.upsert_run({"run_id": "p", "agent_id": "a", "status": "completed",
                   "finished_at": _iso_days_ago(40)})
    rm.update_run("p", {"process": {"tool_calls": [{"tool": "x"}]}})
    assert rm.get_run_process("p").get("tool_calls")
    maintenance.prune_old_runs(retention_days=30)
    assert rm.get_run_process("p") == {}


def test_prune_orphan_files_removes_unknown_logs():
    from common.paths import AGENTS_HUB_ROOT
    logs = AGENTS_HUB_ROOT / "run_logs"
    logs.mkdir(parents=True, exist_ok=True)
    orphan = logs / "agent_run_ORPHAN.log"
    orphan.write_text("stale", encoding="utf-8")

    # A log whose run still exists must be kept.
    rm.upsert_run({"run_id": "LIVE", "agent_id": "a", "status": "running",
                   "log_file": str(logs / "agent_run_LIVE.log")})
    (logs / "agent_run_LIVE.log").write_text("live", encoding="utf-8")

    removed = maintenance.prune_orphan_files()
    assert removed >= 1
    assert not orphan.exists()
    assert (logs / "agent_run_LIVE.log").exists()


def test_run_maintenance_is_daily_guarded():
    first = maintenance.run_maintenance()
    assert "skipped" not in first          # first run executes
    second = maintenance.run_maintenance()
    assert second == {"skipped": 1}        # within 24h → skipped
    forced = maintenance.run_maintenance(force=True)
    assert "skipped" not in forced         # force overrides the guard
