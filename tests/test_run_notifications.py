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
