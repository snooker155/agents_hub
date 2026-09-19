"""Task state-machine tests: dependencies, cascades, container completion."""
import pytest

from tasks import service as ts
from tasks.models import TaskStatus
from tasks.service import DEPENDENCY_BLOCK_PREFIX, CASCADE_BLOCK_PREFIX, PAUSE_MARKER


def test_create_with_unfinished_dependency_is_blocked():
    dep = ts.create_task("dep")
    t = ts.create_task("needs dep", depends=[dep.id])
    assert t.status == TaskStatus.blocked
    assert (t.blocked_reason or "").startswith(DEPENDENCY_BLOCK_PREFIX)


def test_completing_dependency_releases_dependent():
    dep = ts.create_task("dep")
    t = ts.create_task("needs dep", depends=[dep.id])
    assert t.status == TaskStatus.blocked

    ts.update_task(dep.id, status=TaskStatus.done)

    released = ts.get_task(t.id)
    assert released.status == TaskStatus.todo
    assert released.blocked_reason is None


def test_partial_dependency_completion_keeps_block():
    d1 = ts.create_task("d1")
    d2 = ts.create_task("d2")
    t = ts.create_task("needs both", depends=[d1.id, d2.id])
    ts.update_task(d1.id, status=TaskStatus.done)
    still = ts.get_task(t.id)
    assert still.status == TaskStatus.blocked  # d2 still pending


def test_deleting_dependency_never_strands_dependent():
    dep = ts.create_task("dep")
    t = ts.create_task("needs dep", depends=[dep.id])
    ts.delete_task(dep.id)
    released = ts.get_task(t.id)
    assert released.status == TaskStatus.todo
    assert dep.id not in released.depends


def test_dependency_cycle_is_rejected():
    a = ts.create_task("a")
    b = ts.create_task("b", depends=[a.id])
    with pytest.raises(ValueError):
        ts.update_task(a.id, depends=[b.id])


def test_block_cascades_to_descendants_and_unblock_restores():
    parent = ts.create_task("parent")
    child = ts.add_subtask(parent.id, "child")
    grandchild = ts.add_subtask(child.id, "grandchild")

    ts.block_task(parent.id, reason="stop everything")

    assert ts.get_task(child.id).status == TaskStatus.blocked
    assert (ts.get_task(child.id).blocked_reason or "").startswith(CASCADE_BLOCK_PREFIX)
    assert ts.get_task(grandchild.id).status == TaskStatus.blocked

    ts.update_task(parent.id, status=TaskStatus.todo)
    assert ts.get_task(child.id).status == TaskStatus.todo
    assert ts.get_task(grandchild.id).status == TaskStatus.todo


def test_cascade_unblock_distinguishes_manual_block():
    # A parent leaving 'blocked' for a non-todo/ready status runs the cascade
    # *unblock* (which only restores auto-cascaded children) without the
    # todo/ready status cascade (which would reset every subtask). This isolates
    # the marker-distinguishing logic: auto-blocked children are restored, a
    # child blocked for its own reason is left blocked.
    parent = ts.create_task("parent")
    auto = ts.add_subtask(parent.id, "auto")
    manual = ts.add_subtask(parent.id, "manual")
    ts.block_task(manual.id, reason="deliberate hold")   # no cascade marker
    ts.block_task(parent.id, reason="parent hold")        # cascades onto 'auto'

    ts.update_task(parent.id, status=TaskStatus.in_progress)

    assert ts.get_task(auto.id).status == TaskStatus.todo      # cascade-restored
    assert ts.get_task(manual.id).status == TaskStatus.blocked  # own block preserved


def test_parent_completes_when_all_subtasks_done(no_launch):
    parent = ts.create_task("parent")
    a = ts.add_subtask(parent.id, "a")
    b = ts.add_subtask(parent.id, "b")
    ts.update_task(parent.id, status=TaskStatus.in_progress)

    ts.update_task(a.id, status=TaskStatus.done)
    ts.update_task(b.id, status=TaskStatus.done)

    assert ts.get_task(parent.id).status == TaskStatus.done


def test_resume_container_requeues_paused_subtasks(no_launch):
    parent = ts.create_task("parent")
    child = ts.add_subtask(parent.id, "child")
    # Simulate a paused container: child stopped with the pause marker.
    ts.default_store.update(child.id, status=TaskStatus.stopped, blocked_reason=PAUSE_MARKER)
    ts.default_store.update(parent.id, status=TaskStatus.stopped, blocked_reason=PAUSE_MARKER)

    result = ts.resume_container(parent.id)
    assert str(child.id) in result["resumed_subtasks"]
    # Child returned to a runnable state (todo, or dispatched to in_progress by
    # the stubbed launcher) — never left stopped.
    assert ts.get_task(child.id).status in (TaskStatus.todo, TaskStatus.in_progress)


def test_subtask_created_under_blocked_parent_inherits_block():
    parent = ts.create_task("parent")
    ts.block_task(parent.id, reason="hold")
    child = ts.add_subtask(parent.id, "child")
    assert child.status == TaskStatus.blocked
