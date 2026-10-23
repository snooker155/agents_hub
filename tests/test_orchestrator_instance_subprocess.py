"""Auto-orchestration in subprocess mode.

A running orchestrator instance that takes tasks routes the board in either
execution mode; subprocess mode only changes where the agents it picks run.
Two things keep that from doubling up: the instance does not leave a session
continuation behind (its own sweep does the follow-up), and the sweep skips a
task that something else took while it was busy with another one.
"""
from __future__ import annotations

import pytest

from instances import registry, store


def _instance(agent_id: str, take_tasks: bool) -> str:
    inst = registry.ensure_instance(agent_id, kind=store.RESIDENT_KIND, workspace="default",
                                    state="standby")
    store.update(inst["instance_id"], take_tasks=take_tasks)
    return inst["instance_id"]


@pytest.mark.parametrize("agent_id,take_tasks,expected", [
    ("orchestrator", True, True),
    ("orchestrator", False, False),
    ("swe_agent", True, False),
])
def test_only_a_task_taking_orchestrator_instance_skips_the_continuation(
        monkeypatch, agent_id, take_tasks, expected):
    from tools.langchain_tools import _in_task_taking_orchestrator

    monkeypatch.setenv("AGENT_INSTANCE_ID", _instance(agent_id, take_tasks))
    assert _in_task_taking_orchestrator() is expected


def test_outside_an_instance_the_continuation_stays(monkeypatch):
    from tools.langchain_tools import _in_task_taking_orchestrator

    monkeypatch.delenv("AGENT_INSTANCE_ID", raising=False)
    assert _in_task_taking_orchestrator() is False


def test_sweep_skips_a_task_taken_since_it_began(monkeypatch):
    from runtime import instance_run
    from tasks import service as tasks_service
    from tasks import TaskStatus

    task = tasks_service.create_task("route me", status=TaskStatus.ready, workspace="default")
    snapshot = tasks_service.get_task(task.id)
    # Between the snapshot and the run, a subprocess claimed it.
    tasks_service.update_task(task.id, status=TaskStatus.in_progress)
    monkeypatch.setattr(tasks_service, "list_tasks", lambda *a, **k: [snapshot])

    def _no_agent(*_a, **_k):
        raise AssertionError("the orchestrator must not run on a task taken meanwhile")

    monkeypatch.setattr("agents.agent_factory.create_agent", _no_agent)
    assert instance_run.sweep_orchestrator_tasks("inst_x", None, "default") == 0
