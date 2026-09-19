"""
Retry-policy tests for finalize_task_from_run: a failed worker run is
re-dispatched (up to the workspace's max_retries) instead of blocking the task.
"""
from managers import run_manager as rm
from tasks import service as ts
from tasks.models import TaskStatus


def _set_max_retries(monkeypatch, n):
    """Stub workspace metadata so the retry gate sees a given max_retries."""
    import workspace
    monkeypatch.setattr(workspace, "get_workspace_metadata",
                        lambda ws: {"orchestrator": {"max_retries": n}})


def _failed_run(task_id, agent_id="swe_agent", *, status="failed", error="boom"):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, task_id=str(task_id), status="running",
                channel="local", link_to_session=False)
    rm.close_run(rid, status=status, exit_code=1, error=error)
    return rid


def test_failed_run_retries_when_allowed(monkeypatch, no_launch):
    _set_max_retries(monkeypatch, 2)
    t = ts.create_task("work")
    rid = _failed_run(t.id)
    rm.finalize_task_from_run(rid, "failed", 1)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.in_progress
    assert task.retry_count == 1
    assert no_launch == [(str(t.id), "swe_agent")]  # exactly one retry dispatched


def test_retry_appends_failure_to_instruction(monkeypatch):
    _set_max_retries(monkeypatch, 1)
    captured = {}

    def _fake_start_run(task_id, agent_id, params=None, run_id=None):
        captured["params"] = params
        return ("new-run", "sess")

    import agents.agent_launcher as launcher
    monkeypatch.setattr(launcher, "start_run", _fake_start_run)

    t = ts.create_task("work")
    rid = _failed_run(t.id, error="segfault in module X")
    rm.finalize_task_from_run(rid, "failed", 1)

    desc = (captured.get("params") or {}).get("description", "")
    assert "Retry 1/1" in desc
    assert "segfault in module X" in desc


def test_retry_exhausted_blocks_task(monkeypatch, no_launch):
    _set_max_retries(monkeypatch, 2)
    t = ts.create_task("work")
    ts.update_task(t.id, retry_count=2)  # already used both retries
    rid = _failed_run(t.id)
    rm.finalize_task_from_run(rid, "failed", 1)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.blocked
    assert no_launch == []


def test_no_retry_when_disabled(monkeypatch, no_launch):
    _set_max_retries(monkeypatch, 0)
    t = ts.create_task("work")
    rid = _failed_run(t.id)
    rm.finalize_task_from_run(rid, "failed", 1)

    assert ts.get_task(t.id).status == TaskStatus.blocked
    assert no_launch == []


def test_user_stopped_run_not_retried(monkeypatch, no_launch):
    _set_max_retries(monkeypatch, 3)
    t = ts.create_task("work")
    rid = _failed_run(t.id, status="stopped", error="stopped by user")
    rm.finalize_task_from_run(rid, "failed", 1)

    assert no_launch == []
    assert ts.get_task(t.id).status == TaskStatus.blocked


def test_control_agent_run_not_retried(monkeypatch, no_launch):
    _set_max_retries(monkeypatch, 3)
    t = ts.create_task("work", status=TaskStatus.in_progress)
    rid = _failed_run(t.id, agent_id="orchestrator")
    rm.finalize_task_from_run(rid, "failed", 1)

    assert no_launch == []
    assert ts.get_task(t.id).status == TaskStatus.blocked
