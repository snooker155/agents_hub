"""The Decompose button on a task and the create form's "decompose" box, run
in the backend process (routes/task_decompose.py).

The decomposer runs in a thread of the backend. That thread once called
``agent.run(prompt, run_id)``, but every agent's ``run`` takes the instruction
as its only positional argument, so each decomposition died with a TypeError
that ended up only in the run log. The create form's box was stored on the task
and never acted on. A decomposition is two passes: a split, then a review of
the tree the split left.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from tasks import service as ts


def _routes():
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import tasks as task_routes
    return task_routes


class _StrictAgent:
    """The real agents' signature: one positional, the rest by keyword.

    Each run is recorded in ``seen["runs"]``; ``on_run`` stands in for the
    tool calls the model would make (the subtasks it adds)."""

    def __init__(self, seen, on_run=None):
        self.seen = seen
        self.on_run = on_run

    def run(self, instruction: str, **kwargs):
        self.seen.setdefault("runs", []).append({"instruction": instruction, **kwargs})
        if self.on_run:
            self.on_run(len(self.seen["runs"]), instruction)
        return SimpleNamespace(ok=True, agent_output="two subtasks", error=None)


class _InlineThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def _patch(monkeypatch, tmp_path, seen, on_run=None):
    task_routes = _routes()
    from services import jobs
    from routes import task_decompose
    monkeypatch.setattr(jobs, "enabled", lambda: False)
    monkeypatch.setattr(task_routes, "create_workspace_folder", lambda *a, **k: tmp_path)
    monkeypatch.setattr(task_decompose, "create_agent", lambda *a, **k: _StrictAgent(seen, on_run))
    monkeypatch.setattr(task_routes.threading, "Thread", _InlineThread)
    return task_routes, task_decompose


def test_the_decomposer_thread_runs_the_agent_with_the_run_id(monkeypatch, tmp_path):
    seen: dict = {}
    task_routes, _ = _patch(monkeypatch, tmp_path, seen)

    task = ts.create_task("ship the release", workspace="acme")
    out = asyncio.run(task_routes.decompose_task(task.id))

    first = seen["runs"][0]
    assert first["run_id"] == out["run_id"]
    assert first["instruction"].startswith(f"Task ID: {task.id}")
    log = (tmp_path / ".logs" / f"agent_run_{out['run_id']}.log").read_text()
    assert "two subtasks" in log and "error" not in log


def test_a_split_that_creates_nothing_is_not_reviewed(monkeypatch, tmp_path):
    seen: dict = {}
    task_routes, task_decompose = _patch(monkeypatch, tmp_path, seen)

    task = ts.create_task("tiny", workspace="acme")
    asyncio.run(task_routes.decompose_task(task.id))

    assert len(seen["runs"]) == 1
    assert task_decompose.status(task.id)["state"] == "done"


def test_the_review_pass_sees_the_tree_and_can_split_a_subtask(monkeypatch, tmp_path):
    task = ts.create_task("menu page", workspace="acme")
    made: dict = {}

    def on_run(n, instruction):
        if n == 1:
            made["a"] = ts.add_subtask(task.id, "build the whole site")
        else:
            ts.add_subtask(made["a"].id, "write index.html")

    seen: dict = {}
    task_routes, task_decompose = _patch(monkeypatch, tmp_path, seen, on_run)
    asyncio.run(task_routes.decompose_task(task.id))

    assert len(seen["runs"]) == 2
    review = seen["runs"][1]["instruction"]
    assert "REVIEW PASS" in review and str(made["a"].id) in review
    tree = asyncio.run(task_routes.decompose_status(task.id))
    assert [(n["title"], n["depth"]) for n in tree["tree"]] == [
        ("build the whole site", 1), ("write index.html", 2)]
    state = tree["status"]
    assert state["state"] == "done" and state["pass"] == "review" and len(state["run_ids"]) == 2
    # Decomposing does not run the task, so the decomposer is not left on it.
    assert not ts.get_task(task.id).assigned_agent_type


def test_a_second_decomposition_waits_for_the_first(monkeypatch, tmp_path):
    from fastapi import HTTPException

    task_routes = _routes()
    from routes import task_decompose
    monkeypatch.setattr(task_routes, "create_workspace_folder", lambda *a, **k: tmp_path)

    class _Held:
        def __init__(self, target, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(task_routes.threading, "Thread", _Held)
    task = ts.create_task("menu page", workspace="acme")
    asyncio.run(task_routes.decompose_task(task.id))
    try:
        asyncio.run(task_routes.decompose_task(task.id))
    except HTTPException as e:
        assert e.status_code == 409
    else:
        raise AssertionError("a second decomposition started")
    assert task_decompose.status(task.id)["state"] == "running"


def test_the_decompose_box_on_create_starts_the_decomposer(monkeypatch, tmp_path):
    """The create form's box used to be stored on the task and never acted on."""
    seen: dict = {}
    task_routes, _ = _patch(monkeypatch, tmp_path, seen)
    from models import TaskCreate

    out = asyncio.run(task_routes.create_task(TaskCreate(
        title="menu page", description="prices", workspace="acme", should_decompose=True)))

    assert out["id"] in seen["runs"][0]["instruction"]


def test_a_task_without_the_box_is_not_decomposed(monkeypatch, tmp_path):
    seen: dict = {}
    task_routes, _ = _patch(monkeypatch, tmp_path, seen)
    from models import TaskCreate

    asyncio.run(task_routes.create_task(TaskCreate(title="menu page", workspace="acme")))

    assert "runs" not in seen


def test_the_channel_must_be_a_decompose_channel(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from models import DecomposeRequest

    seen: dict = {}
    task_routes, _ = _patch(monkeypatch, tmp_path, seen)
    task = ts.create_task("menu page", workspace="acme")
    try:
        asyncio.run(task_routes.decompose_task(task.id, DecomposeRequest(channel="chat:x")))
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("a foreign channel was accepted")
