"""The Decompose button on a task, run in the backend process.

Without runner replicas the decomposer runs in a thread of the backend. That
thread called ``agent.run(prompt, run_id)``, but every agent's ``run`` takes the
instruction as its only positional argument, so each decomposition died with a
TypeError that ended up only in the run log.
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
    """The real agents' signature: one positional, the rest by keyword."""

    def __init__(self, seen):
        self.seen = seen

    def run(self, instruction: str, **kwargs):
        self.seen.update(instruction=instruction, **kwargs)
        return SimpleNamespace(ok=True, agent_output="two subtasks", error=None)


class _InlineThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def test_the_decomposer_thread_runs_the_agent_with_the_run_id(monkeypatch, tmp_path):
    task_routes = _routes()
    from services import jobs

    seen: dict = {}
    monkeypatch.setattr(jobs, "enabled", lambda: False)
    monkeypatch.setattr(task_routes, "create_workspace_folder", lambda *a, **k: tmp_path)
    monkeypatch.setattr(task_routes, "create_agent", lambda *a, **k: _StrictAgent(seen))
    monkeypatch.setattr(task_routes.threading, "Thread", _InlineThread)

    task = ts.create_task("ship the release", workspace="acme")
    out = asyncio.run(task_routes.decompose_task(task.id))

    assert seen["run_id"] == out["run_id"]
    assert str(task.id) in seen["instruction"]
    log = (tmp_path / ".logs" / f"agent_run_{out['run_id']}.log").read_text()
    assert "two subtasks" in log and "error" not in log
