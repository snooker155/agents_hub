"""assign_flow_tool (tools/langchain_tools.py): the orchestrator puts a saved
flow on a task as its executor, after the user approved it.

What is promised: the call refuses until ``user_approved`` is true; a flow of
another workspace reads as missing; a stopped task is refused; an approved
call makes the flow the task's executor (kind flow), starts its run and moves
the task to in_progress, returning the run id; the tool is on the orchestrator
and is not a delegation edge for the capability guard.

Run: ``python -m pytest tests/test_assign_flow_tool.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspace_scope_coordination import run_in, two_ws  # noqa: E402,F401 - fixture


@pytest.fixture
def flow_a(two_ws):  # noqa: F811 - the fixture above
    from flow import store as flow_store
    flow_store.save_flow({
        "id": "flow-a", "name": "flow-a", "description": "", "workspace": "ws_a", "task_id": None,
        "nodes": [{"id": "n1", "type": "flowNode", "position": {"x": 0, "y": 0},
                   "data": {"label": "agent_a", "agent_id": "agent_a", "description": ""}}],
        "edges": [],
    })
    yield "flow-a"
    flow_store.delete_flow("flow-a")


@pytest.fixture
def task(two_ws):  # noqa: F811 - the fixture above
    from tasks import service as ts
    return ts.create_task(title="Build it", workspace="ws_a")


@pytest.fixture
def launcher(monkeypatch):
    """The flow subprocess never starts in a test: the launcher records the call."""
    from flow import launcher as flow_launcher
    calls = []

    def fake_start(task_id, flow_id, params=None, **kw):
        calls.append((task_id, flow_id, params))
        return "run-1", "session-1"
    monkeypatch.setattr(flow_launcher, "start_flow_run", fake_start)
    return calls


def _call(**kw):
    from tools.langchain_tools import assign_flow_tool
    return json.loads(assign_flow_tool.invoke(kw))


def test_it_refuses_until_the_user_approved(task, flow_a, launcher):
    with run_in("ws_a"):
        out = _call(task_id=str(task.id), flow_id=flow_a)
    assert out["ok"] is False and out["code"] == "approval_required"
    assert launcher == []


def test_an_approved_call_makes_the_flow_the_executor_and_starts_it(task, flow_a, launcher):
    from tasks import service as ts
    with run_in("ws_a"):
        out = _call(task_id=str(task.id), flow_id=flow_a, user_approved=True, params_json='{"goal": "x"}')
    assert out["ok"] is True, out
    assert out["run_id"] == "run-1"
    assert launcher == [(str(task.id), "flow-a", {"goal": "x"})]
    t = ts.get_task(task.id)
    assert t.executor is not None and t.executor.kind == "flow" and t.executor.id == "flow-a"
    assert str(t.status) .endswith("in_progress")


def test_another_workspaces_flow_reads_as_missing(task, flow_a, launcher):
    with run_in("ws_b"):
        out = _call(task_id=str(task.id), flow_id=flow_a, user_approved=True)
    assert out["ok"] is False and out["code"] == "not_found"
    assert launcher == []


def test_a_stopped_task_is_refused(task, flow_a, launcher):
    from tasks import service as ts
    from tasks import TaskStatus
    ts.update_task(task.id, status=TaskStatus.stopped)
    with run_in("ws_a"):
        out = _call(task_id=str(task.id), flow_id=flow_a, user_approved=True)
    assert out["ok"] is False and out["code"] == "invalid_task"
    assert launcher == []


def test_the_orchestrator_holds_it_and_it_is_not_an_edge():
    import json as _json
    from common.bootstrap import BOOTSTRAP_AGENTS_FILE
    from tools.capabilities import DELEGATING_TOOLS
    raw = _json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
    recs = raw if isinstance(raw, list) else raw.get("agents", raw)
    orchestrator = next(r for r in recs if r["id"] == "orchestrator")
    assert "assign_flow_tool" in orchestrator["tools"]
    assert "assign_flow_tool" not in DELEGATING_TOOLS
