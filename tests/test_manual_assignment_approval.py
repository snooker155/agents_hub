"""Manual assignment mode: an agent's choice waits for a person.

The orchestrator assigns, the task goes to ``pending`` with a run awaiting
approval, the person is told, and the same run cannot then start it itself.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from test_workspace_scope_coordination import run_in, two_ws  # noqa: F401 - fixture


def call(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


@contextmanager
def in_run(run_id):
    from common.agent_context import current_run_id
    token = current_run_id.set(run_id)
    try:
        yield
    finally:
        current_run_id.reset(token)


@pytest.fixture
def task(two_ws):  # noqa: F811 - the fixture above
    from tasks import service as ts
    from tasks import TaskStatus
    t = ts.create_task(title="About page", workspace="ws_a")
    # The orchestrator instance's sweep marks the task in progress first.
    ts.update_task(t.id, status=TaskStatus.in_progress)
    return t


def _inbox():
    from plans import service as plan_service
    return [n for n in plan_service.list_notifications() if n.workspace == "ws_a"]


def test_the_assigning_run_cannot_start_what_awaits_approval(task, monkeypatch):
    from tasks import service as ts
    from tasks import TaskStatus
    from tools.langchain_tools import assign_agent_tool, start_agent_tool

    started = []
    monkeypatch.setattr("tools.langchain_tools.rm_start_run",
                        lambda *a, **k: started.append(a) or ("r", None))
    with run_in("ws_a", agent_id="orchestrator"), in_run("orch-run-1"):
        out = call(assign_agent_tool, task_id=str(task.id), agent_id="agent_a", reason="fits")
        assert out["ok"] is True, out
        assert out["assignment_mode"] == "manual"
        assert ts.get_task(task.id).status == TaskStatus.pending

        again = call(assign_agent_tool, task_id=str(task.id), agent_id="helper")
        assert again["assignment_mode"] == "manual" and "STOP" in again["message"]

        refused = call(start_agent_tool, task_id=str(task.id))
        assert refused["code"] == "awaiting_approval"
    assert started == []
    assert ts.get_task(task.id).status == TaskStatus.pending

    # The person's "yes" in a later chat turn is a run of its own.
    with run_in("ws_a", agent_id="orchestrator"), in_run("chat-turn-2"):
        assert call(start_agent_tool, task_id=str(task.id))["ok"] is True
    assert len(started) == 1


def test_the_person_is_asked_to_approve(task):
    from tools.langchain_tools import assign_agent_tool

    with run_in("ws_a", agent_id="orchestrator"), in_run("orch-run-1"):
        call(assign_agent_tool, task_id=str(task.id), agent_id="agent_a", reason="it writes copy")
    asks = [n for n in _inbox() if n.title == "Approval needed: About page"]
    assert len(asks) == 1
    assert "agent_a" in asks[0].body and "it writes copy" in asks[0].body
    assert asks[0].source.get("task_id") == str(task.id)
    # The dashboard builds the text in the reader's language from this.
    assert asks[0].source["message"] == {
        "key": "approvalNeeded",
        "params": {"title": "About page", "agent": "agent_a", "reason": "it writes copy"},
    }


def test_a_failed_routing_run_reaches_the_inbox_and_a_good_one_does_not(task):
    from managers.runs.notifications import _notify_task_run_finished

    base = {"task_id": str(task.id), "agent_id": "orchestrator", "run_id": "o1"}
    _notify_task_run_finished({"status": "running"}, {**base, "status": "completed"})
    assert not [n for n in _inbox() if "About page" in n.title]
    _notify_task_run_finished({"status": "running"},
                              {**base, "status": "failed", "error": "model unavailable"})
    failed = [n for n in _inbox() if n.title == "Routing failed: About page"]
    assert len(failed) == 1 and "model unavailable" in failed[0].body
