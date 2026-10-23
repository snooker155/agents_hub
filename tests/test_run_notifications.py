"""Inbox notifications emitted by the run lifecycle.

A task is re-run many times over its life (the orchestrator re-routes it, the
executor retries, a reviewer picks it up), and every one of those runs used to
emit its own "Task started: <title>" entry. Identical text, fresh unread row —
the inbox looked like read messages were lighting up again.
"""
from __future__ import annotations

import pytest

from managers import run_manager as rm
from tasks import service as ts


@pytest.fixture
def inbox(monkeypatch):
    """Capture every notification the run hooks create."""
    from plans import service as plan_service

    created = []

    def _capture(**kwargs):
        created.append(kwargs)
        return None

    monkeypatch.setattr(plan_service, "create_notification", _capture)
    return created


def _start(task_id, agent_id):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, task_id=str(task_id), status="running", link_to_session=False)
    return rid


def test_task_start_announced_once_across_reruns(inbox):
    task = ts.create_task("Skill Management Module")

    _start(task.id, "orchestrator")   # routing run: announces nothing
    _start(task.id, "swe_agent")      # first executor run: the one entry
    _start(task.id, "orchestrator")   # re-route
    _start(task.id, "swe_agent")      # retry
    _start(task.id, "code_reviewer")  # review

    titles = [n["title"] for n in inbox]
    assert titles == ["Task started: Skill Management Module"]
    assert inbox[0]["body"] == "Agent 'swe_agent' started the task."


def test_distinct_tasks_each_get_their_own_entry(inbox):
    a = ts.create_task("Alpha")
    b = ts.create_task("Beta")
    _start(a.id, "swe_agent")
    _start(b.id, "swe_agent")
    assert [n["title"] for n in inbox] == ["Task started: Alpha", "Task started: Beta"]


def test_orchestrator_only_task_stays_silent(inbox):
    task = ts.create_task("Routing only")
    _start(task.id, "orchestrator")
    assert inbox == []


def test_chat_run_without_task_notifies_nothing(inbox):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=None, session_type="chat", status="running",
                link_to_session=False)
    assert inbox == []


def _finish(run_id, status="completed"):
    rm.close_run(run_id, status=status, exit_code=0 if status == "completed" else 1, output="done")


def test_a_task_reset_and_run_again_is_announced_again(inbox):
    task = ts.create_task("Menu page")
    _start(task.id, "swe_agent")
    ts.update_task(task.id, status="in_progress")
    ts.update_task(task.id, status="resolved")
    ts.update_task(task.id, status="todo")   # the person resets it to run it again
    _start(task.id, "swe_agent")
    assert [n["title"] for n in inbox] == ["Task started: Menu page"] * 2


def test_the_decomposer_does_not_take_the_start_away(inbox):
    task = ts.create_task("Split me")
    _finish(_start(task.id, "decomposer"))   # splitting is not the work
    _start(task.id, "swe_agent")
    assert [n["title"] for n in inbox] == ["Task started: Split me"]


def test_a_subtask_of_a_persons_task_announces_its_end(inbox):
    from tasks.models import CreatedBy
    parent = ts.create_task("Menu page")
    sub = ts.add_subtask(parent.id, "Write the HTML")
    ts.update_task(sub.id, created_by=CreatedBy.orchestrator)
    _finish(_start(sub.id, "swe_agent"))
    assert "Task completed: Write the HTML" in [n["title"] for n in inbox]


def test_a_service_agent_finishing_announces_nothing(inbox):
    task = ts.create_task("Split me")
    _finish(_start(task.id, "decomposer"))
    assert inbox == []


def test_a_parent_completed_by_its_subtasks_is_announced(inbox):
    parent = ts.create_task("Menu page")
    a = ts.add_subtask(parent.id, "Part one")
    b = ts.add_subtask(parent.id, "Part two")
    ts.update_task(a.id, status="done")
    ts.update_task(b.id, status="done")
    assert ts.get_task(parent.id).status.value == "done"
    done = [n for n in inbox if n["title"] == "Task completed: Menu page"]
    assert len(done) == 1 and done[0]["body"] == "All 2 subtasks are done."
