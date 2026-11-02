"""``delete_task``: an agent deletes a task with its subtasks, behind a card
(tools/task_management.py, tools/approval.py)."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest


def call(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


@contextmanager
def run_in(workspace, task_id=None):
    """A tool call in a run of ``workspace``, working on ``task_id`` if given."""
    from common.agent_context import current_task_id
    from common.workspace_context import _workspace_ctx

    ws_token = _workspace_ctx.set(workspace)
    task_token = current_task_id.set(str(task_id) if task_id else None)
    try:
        yield
    finally:
        current_task_id.reset(task_token)
        _workspace_ctx.reset(ws_token)


@pytest.fixture
def ws(monkeypatch):
    from workspace import create_workspace_folder
    monkeypatch.delenv("AGENT_TASK_ID", raising=False)
    for name in ("ws_a", "ws_b"):
        create_workspace_folder(name)
    yield
    from common.workspace_context import _workspace_ctx
    _workspace_ctx.set(None)


def test_deletes_the_task_with_its_subtasks_and_frees_dependents(ws):
    from tasks.service import add_subtask, create_task, get_task, update_task
    from tools.task_management import delete_task

    parent = create_task(title="Parent", workspace="ws_a")
    child = add_subtask(parent.id, title="Child")
    waiting = create_task(title="Waits", workspace="ws_a")
    update_task(waiting.id, depends=[parent.id])

    with run_in("ws_a"):
        out = call(delete_task, id=str(parent.id))
    assert out["ok"] is True and out["deleted"] == 2
    assert out["task"]["title"] == "Parent"
    assert get_task(parent.id) is None and get_task(child.id) is None
    assert parent.id not in (get_task(waiting.id).depends or [])


def test_refuses_another_workspaces_task(ws):
    from tasks.service import create_task, get_task
    from tools.task_management import delete_task

    other = create_task(title="Theirs", workspace="ws_b")
    with run_in("ws_a"):
        out = call(delete_task, id=str(other.id))
    assert out["ok"] is False and out["code"] == "not_found"
    assert get_task(other.id) is not None


def test_refuses_the_runs_own_task_and_its_ancestors(ws):
    from tasks.service import add_subtask, create_task, get_task
    from tools.task_management import delete_task

    parent = create_task(title="Parent", workspace="ws_a")
    child = add_subtask(parent.id, title="Child")
    with run_in("ws_a", task_id=child.id):
        for tid in (child.id, parent.id):
            out = call(delete_task, id=str(tid))
            assert out["ok"] is False and out["code"] == "forbidden"
    assert get_task(parent.id) is not None and get_task(child.id) is not None


def test_refuses_a_task_an_agent_works_on(ws):
    from tasks.models import TaskStatus
    from tasks.service import add_subtask, create_task, get_task, update_task
    from tools.task_management import delete_task

    parent = create_task(title="Parent", workspace="ws_a")
    child = add_subtask(parent.id, title="Child")
    update_task(child.id, status=TaskStatus.in_progress)
    with run_in("ws_a"):
        out = call(delete_task, id=str(parent.id))
    assert out["ok"] is False and out["code"] == "conflict"
    assert out["in_progress"] == [child.key or str(child.id)]
    assert get_task(parent.id) is not None


def test_needs_a_yes_and_the_card_names_the_task(ws):
    from tasks.service import add_subtask, create_task
    from tools.approval import NEEDS_APPROVAL, describe_call

    assert "delete_task" in NEEDS_APPROVAL
    parent = create_task(title="Parent", workspace="ws_a")
    add_subtask(parent.id, title="Child")
    with run_in("ws_a"):
        sentence = describe_call("delete_task", {"id": str(parent.id)})
        assert "“Parent”" in sentence and "1 subtask," in sentence
        assert describe_call("delete_task", json.dumps({"id": "nonsense"})) == ""
    with run_in("ws_b"):
        assert describe_call("delete_task", {"id": str(parent.id)}) == ""


def test_is_in_the_task_tool_group():
    from tools.registry import get_all_tools

    spec = next(s for s in get_all_tools() if s.id == "delete_task")
    assert spec.category == "task_management"
