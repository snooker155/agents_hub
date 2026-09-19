"""finalize_task_from_run decision-matrix tests (single-mode, hermetic).

These cover the branch logic that turns a finished run into task-status
progress — the pipeline's brainstem, previously exercised only in production.
"""
from managers import run_manager as rm
from tasks import service as ts
from tasks.models import TaskStatus


def _run_for_task(task_id, agent_id, *, channel="local"):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, task_id=str(task_id), status="running",
                channel=channel, link_to_session=False)
    return rid


def test_worker_completed_resolves_and_clears_agent():
    t = ts.create_task("work")
    ts.assign_agent(t.id, "swe_agent", None, run_id="r")
    rid = _run_for_task(t.id, "swe_agent")
    rm.close_run(rid, status="completed", exit_code=0)
    rm.finalize_task_from_run(rid, "completed", 0)

    done = ts.get_task(t.id)
    assert done.status == TaskStatus.resolved
    assert done.assigned_agent_type is None


def test_worker_failed_blocks_task_with_reason():
    t = ts.create_task("work")
    rid = _run_for_task(t.id, "swe_agent")
    rm.close_run(rid, status="failed", exit_code=1, error="compile boom")
    rm.finalize_task_from_run(rid, "failed", 1)

    blocked = ts.get_task(t.id)
    assert blocked.status == TaskStatus.blocked
    assert "compile boom" in (blocked.blocked_reason or "")


def test_orchestrator_completed_does_not_resolve_worker_task():
    # The orchestrator is a non-resolving agent: a normal (non-continuation)
    # completed orchestrator run must not flip the task to resolved.
    t = ts.create_task("work", status=TaskStatus.in_progress)
    rid = _run_for_task(t.id, "orchestrator", channel="local")
    rm.close_run(rid, status="completed", exit_code=0)
    rm.finalize_task_from_run(rid, "completed", 0)

    assert ts.get_task(t.id).status == TaskStatus.in_progress


def test_agent_set_terminal_status_is_not_overwritten():
    # A worker that already set a terminal status (e.g. reviewer set 'blocked')
    # must not be overwritten to resolved by finalize.
    t = ts.create_task("work", status=TaskStatus.blocked)
    rid = _run_for_task(t.id, "swe_agent")
    rm.close_run(rid, status="completed", exit_code=0)
    rm.finalize_task_from_run(rid, "completed", 0)

    assert ts.get_task(t.id).status == TaskStatus.blocked
