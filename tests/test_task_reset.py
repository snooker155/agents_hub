"""A task moved back to todo starts over (tasks/service.py update_task): its
results, gradings and execution history go, the runs and sessions stay."""
from __future__ import annotations

from managers import run_manager as rm
from tasks import service as ts
from tasks.context import build_task_instruction
from tasks.models import TaskStatus


def _run(task_id, agent_id="swe_agent"):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, task_id=str(task_id), status="running", link_to_session=False)
    rm.close_run(rid, status="completed", exit_code=0, output="done")
    return rid


def test_back_to_todo_clears_the_history_and_keeps_the_runs():
    task = ts.create_task("Menu page")
    rid = _run(task.id)
    ts.set_task_result(task.id, "made index.html", run_id=rid, agent_id="swe_agent")
    ts.update_task(task.id, status=TaskStatus.in_progress,
                   outcome_evaluations=[{"passed": False, "trigger": "run"}])
    ts.update_task(task.id, status=TaskStatus.resolved)
    assert ts.get_task_runs(task.id)

    ts.update_task(task.id, status=TaskStatus.todo)

    fresh = ts.get_task(task.id)
    assert fresh.outcome_evaluations == []
    assert ts.get_task_runs(task.id) == []
    assert ts.get_task_results(task.id) == []
    assert rm.get_run_by_id(rid) is not None          # the run itself is kept
    assert "history_cleared" in [e.get("type") for e in ts.get_task_activity_log(task.id)]


def test_resetting_the_parent_clears_its_subtasks_too():
    parent = ts.create_task("Menu page")
    sub = ts.add_subtask(parent.id, "Write the HTML")
    _run(sub.id)
    ts.update_task(sub.id, status=TaskStatus.in_progress)
    ts.update_task(sub.id, status=TaskStatus.done)
    ts.update_task(parent.id, status=TaskStatus.todo)
    assert ts.get_task(sub.id).status == TaskStatus.todo
    assert ts.get_task_runs(sub.id) == []


def test_a_worker_is_told_to_make_the_files_and_not_ask(tmp_path):
    task = ts.create_task("Create menu.html")
    text = build_task_instruction(str(task.id), "go", work_dir=str(tmp_path))
    assert "HOW THIS RUN WORKS" in text
    assert str(tmp_path) in text and "even when it is empty" in text


def test_routing_and_review_get_no_such_note():
    task = ts.create_task("Create menu.html")
    assert "HOW THIS RUN WORKS" not in build_task_instruction(str(task.id), "split it", worker=False)


def test_the_worker_is_told_to_check_its_work_and_report_a_status(tmp_path):
    task = ts.create_task("Create menu.html")
    text = build_task_instruction(str(task.id), "go", work_dir=str(tmp_path))
    assert "check your own work" in text
    assert "STATUS: done" in text and "STATUS: blocked" in text


def test_the_status_line_is_read_from_the_end_of_the_answer():
    from tasks.context import self_reported_status
    assert self_reported_status("Made it.\nSTATUS: done") == ("done", "")
    assert self_reported_status("**STATUS: blocked: no browser**") == ("blocked", "no browser")
    assert self_reported_status("STATUS: Blocked - нет браузера") == ("blocked", "нет браузера")
    assert self_reported_status("No status here") == (None, "")
