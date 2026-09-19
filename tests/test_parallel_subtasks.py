"""Parallel sibling-subtask execution (opt-in) + atomic claim safety."""
import pytest

from tasks import service as ts
from tasks.models import TaskStatus


def _count(children, status):
    return sum(1 for c in children if ts.get_task(c.id).status == status)


def test_default_cap_is_one_at_a_time(no_launch):
    parent = ts.create_task("parent")
    subs = [ts.add_subtask(parent.id, f"s{i}") for i in range(3)]
    dispatched = ts.activate_parent_container(parent.id)
    assert dispatched == 1
    assert _count(subs, TaskStatus.in_progress) == 1
    assert _count(subs, TaskStatus.todo) == 2


def test_raised_cap_dispatches_independent_subtasks_in_parallel(no_launch, monkeypatch):
    monkeypatch.setattr(ts, "_max_parallel_subtasks", lambda ws: 3)
    parent = ts.create_task("parent")
    subs = [ts.add_subtask(parent.id, f"s{i}") for i in range(3)]
    dispatched = ts.activate_parent_container(parent.id)
    assert dispatched == 3
    assert _count(subs, TaskStatus.in_progress) == 3


def test_cap_respects_currently_active_slots(no_launch, monkeypatch):
    monkeypatch.setattr(ts, "_max_parallel_subtasks", lambda ws: 2)
    parent = ts.create_task("parent")
    subs = [ts.add_subtask(parent.id, f"s{i}") for i in range(4)]
    ts.activate_parent_container(parent.id)          # fills 2 slots
    assert _count(subs, TaskStatus.in_progress) == 2
    # A second promotion pass while 2 are active dispatches nothing more.
    assert ts.promote_runnable_subtasks(parent.id) == 0
    assert _count(subs, TaskStatus.in_progress) == 2


def test_dependencies_are_respected_under_parallelism(no_launch, monkeypatch):
    monkeypatch.setattr(ts, "_max_parallel_subtasks", lambda ws: 5)
    parent = ts.create_task("parent")
    a = ts.add_subtask(parent.id, "a")
    b = ts.add_subtask(parent.id, "b", depends=[a.id])   # gated behind a
    c = ts.add_subtask(parent.id, "c")
    ts.activate_parent_container(parent.id)
    # a and c are independent → dispatched; b waits on a.
    assert ts.get_task(a.id).status == TaskStatus.in_progress
    assert ts.get_task(c.id).status == TaskStatus.in_progress
    assert ts.get_task(b.id).status == TaskStatus.blocked

    # Finishing a releases b, and the next promotion picks it up.
    ts.update_task(a.id, status=TaskStatus.done)
    assert ts.get_task(b.id).status == TaskStatus.in_progress


def test_atomic_claim_prevents_double_dispatch():
    t = ts.create_task("solo")
    first = ts.default_store.claim(
        t.id, waiting_statuses=("todo", "ready"),
        assigned_agent_type="orchestrator", status=TaskStatus.in_progress)
    second = ts.default_store.claim(
        t.id, waiting_statuses=("todo", "ready"),
        assigned_agent_type="orchestrator", status=TaskStatus.in_progress)
    assert first is not None      # winner
    assert second is None         # loser — already claimed


def test_claim_rejects_non_waiting_status():
    t = ts.create_task("t", status=TaskStatus.in_progress)
    assert ts.default_store.claim(t.id, waiting_statuses=("todo",), status=TaskStatus.in_progress) is None


def test_dispatch_releases_claim_when_launch_fails(monkeypatch):
    parent = ts.create_task("parent")
    child = ts.add_subtask(parent.id, "child")

    import agents.agent_launcher as launcher
    def _boom(*a, **k):
        raise RuntimeError("launch failed")
    monkeypatch.setattr(launcher, "start_run", _boom)

    ok = ts._start_orchestrator_on_subtask(ts.get_task(child.id), store=ts.default_store)
    assert ok is False
    # Claim released so the subtask can be retried, not stranded as in_progress.
    reloaded = ts.get_task(child.id)
    assert reloaded.status == TaskStatus.todo
    assert reloaded.assigned_agent_type is None
