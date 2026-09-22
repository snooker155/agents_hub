"""The task status transition table: closure, actor gating, and error shape."""
import pytest

from tasks import service as ts
from tasks.models import (
    TRANSITIONS,
    AGENT_TRANSITIONS,
    Actor,
    TaskStatus,
    IllegalTransition,
    check_transition,
)


# -------------------- table closure --------------------

def test_agent_transitions_are_a_subset_of_the_full_table():
    """Every move AGENT_TRANSITIONS grants must also be a real system-level edge."""
    for frm, tos in AGENT_TRANSITIONS.items():
        for to in tos:
            assert to in TRANSITIONS.get(frm, frozenset()), (
                f"agent transition {frm}->{to} is not in TRANSITIONS"
            )


def test_user_targets_intersected_with_the_table_is_non_empty_for_common_moves():
    """USER_TARGETS narrows destinations; it must not accidentally forbid the
    ordinary moves the dashboard relies on (todo->ready, in_progress->blocked, etc.)."""
    common_moves = [
        (TaskStatus.todo, TaskStatus.ready),
        (TaskStatus.todo, TaskStatus.blocked),
        (TaskStatus.in_progress, TaskStatus.blocked),
        (TaskStatus.blocked, TaskStatus.in_progress),
        (TaskStatus.in_progress, TaskStatus.stopped),
        (TaskStatus.reviewing, TaskStatus.reviewed),  # a "system" move (see below)
    ]
    for frm, to in common_moves:
        assert to in TRANSITIONS.get(frm, frozenset())


def test_every_transition_update_task_performs_is_in_the_table():
    """Exercise the real cascades/paths and check each status change against
    the table directly (a closed-world sanity net for the table itself)."""
    exercised = []

    dep = ts.create_task("dep")
    ts.create_task("needs dep", depends=[dep.id])
    exercised.append((TaskStatus.todo, TaskStatus.blocked))  # created blocked
    ts.update_task(dep.id, status=TaskStatus.done)
    exercised.append((TaskStatus.todo, TaskStatus.done))
    exercised.append((TaskStatus.blocked, TaskStatus.todo))  # t released

    parent = ts.create_task("parent")
    child = ts.add_subtask(parent.id, "child")
    ts.update_task(parent.id, status=TaskStatus.in_progress)
    exercised.append((TaskStatus.todo, TaskStatus.in_progress))
    ts.update_task(child.id, status=TaskStatus.done)
    exercised.append((TaskStatus.todo, TaskStatus.done))
    exercised.append((TaskStatus.in_progress, TaskStatus.done))  # parent auto-completes

    for frm, to in exercised:
        assert to in TRANSITIONS.get(frm, frozenset()), f"{frm}->{to} missing from TRANSITIONS"


# -------------------- actor gating --------------------

def test_system_actor_may_perform_any_tabled_transition():
    t = ts.create_task("work")
    updated = ts.update_task(t.id, status=TaskStatus.awaiting_input, actor=Actor.system)
    assert updated.status == TaskStatus.awaiting_input
    updated = ts.update_task(t.id, status=TaskStatus.in_progress, actor="system")
    assert updated.status == TaskStatus.in_progress


def test_agent_cannot_set_done():
    t = ts.create_task("work", status=TaskStatus.in_progress)
    with pytest.raises(IllegalTransition):
        ts.update_task(t.id, status=TaskStatus.done, actor=Actor.agent)
    assert ts.get_task(t.id).status == TaskStatus.in_progress


def test_agent_cannot_set_reviewed_reviewing_or_pending_from_in_progress():
    for target in (TaskStatus.reviewed, TaskStatus.reviewing, TaskStatus.pending):
        t = ts.create_task("work", status=TaskStatus.in_progress)
        with pytest.raises(IllegalTransition):
            ts.update_task(t.id, status=target, actor=Actor.agent)


def test_reviewer_agent_records_its_verdict():
    """The code_reviewer agent moves a resolved task to reviewing and then to
    reviewed, or back to in_progress with findings; those are the only routes
    an agent has into the review states."""
    t = ts.create_task("work", status=TaskStatus.resolved)
    assert ts.update_task(t.id, status=TaskStatus.reviewing, actor=Actor.agent).status == TaskStatus.reviewing
    assert ts.update_task(t.id, status=TaskStatus.reviewed, actor=Actor.agent).status == TaskStatus.reviewed
    t2 = ts.create_task("work", status=TaskStatus.reviewing)
    assert ts.update_task(t2.id, status=TaskStatus.in_progress, actor=Actor.agent).status == TaskStatus.in_progress
    with pytest.raises(IllegalTransition):
        ts.update_task(t2.id, status=TaskStatus.reviewed, actor=Actor.agent)


def test_agent_cannot_stop_a_task():
    t = ts.create_task("work", status=TaskStatus.in_progress)
    with pytest.raises(IllegalTransition):
        ts.update_task(t.id, status=TaskStatus.stopped, actor=Actor.agent)


def test_agent_allowed_moves_succeed():
    t = ts.create_task("work")  # todo
    updated = ts.update_task(t.id, status=TaskStatus.ready, actor=Actor.agent)
    assert updated.status == TaskStatus.ready

    t2 = ts.create_task("work2", status=TaskStatus.in_progress)
    updated = ts.update_task(t2.id, status=TaskStatus.blocked, blocked_reason="stuck", actor=Actor.agent)
    assert updated.status == TaskStatus.blocked
    updated = ts.update_task(t2.id, status=TaskStatus.in_progress, actor=Actor.agent)
    assert updated.status == TaskStatus.in_progress

    t3 = ts.create_task("work3", status=TaskStatus.in_progress)
    updated = ts.update_task(t3.id, status=TaskStatus.resolved, actor=Actor.agent)
    assert updated.status == TaskStatus.resolved


def test_agent_can_pick_up_its_own_work():
    # An agent starting on a task it was given — todo/ready -> in_progress —
    # is a normal move, not one the system alone should gate.
    t = ts.create_task("work")  # todo
    updated = ts.update_task(t.id, status=TaskStatus.in_progress, actor=Actor.agent)
    assert updated.status == TaskStatus.in_progress

    t2 = ts.create_task("work2", status=TaskStatus.ready)
    updated = ts.update_task(t2.id, status=TaskStatus.in_progress, actor=Actor.agent)
    assert updated.status == TaskStatus.in_progress


def test_agent_cannot_jump_straight_to_blocked_from_todo():
    # in_progress->blocked is agent-allowed; todo->blocked is not, even though
    # it IS a valid system/user transition.
    t = ts.create_task("work")  # todo
    assert TaskStatus.blocked in TRANSITIONS[TaskStatus.todo]  # sanity: valid for system
    with pytest.raises(IllegalTransition):
        ts.update_task(t.id, status=TaskStatus.blocked, blocked_reason="x", actor=Actor.agent)


def test_user_cannot_set_awaiting_input():
    t = ts.create_task("work", status=TaskStatus.in_progress)
    with pytest.raises(IllegalTransition):
        ts.update_task(t.id, status=TaskStatus.awaiting_input, actor=Actor.user)


def test_user_cannot_set_awaiting_approval_or_reviewing_or_pending():
    for target in (TaskStatus.awaiting_approval, TaskStatus.reviewing, TaskStatus.pending):
        t = ts.create_task("work", status=TaskStatus.in_progress)
        with pytest.raises(IllegalTransition):
            ts.update_task(t.id, status=target, actor=Actor.user)


def test_user_allowed_moves_succeed():
    t = ts.create_task("work")  # todo
    updated = ts.update_task(t.id, status=TaskStatus.in_progress, actor=Actor.user)
    assert updated.status == TaskStatus.in_progress
    updated = ts.update_task(t.id, status=TaskStatus.blocked, actor=Actor.user)
    assert updated.status == TaskStatus.blocked
    updated = ts.update_task(t.id, status=TaskStatus.stopped, actor=Actor.user)
    assert updated.status == TaskStatus.stopped


def test_system_can_do_what_user_and_agent_cannot():
    t = ts.create_task("work", status=TaskStatus.in_progress)
    updated = ts.update_task(t.id, status=TaskStatus.awaiting_approval, actor=Actor.system)
    assert updated.status == TaskStatus.awaiting_approval
    updated = ts.update_task(t.id, status=TaskStatus.in_progress, actor=Actor.system)
    updated = ts.update_task(t.id, status=TaskStatus.reviewing, actor=Actor.system)
    assert updated.status == TaskStatus.reviewing


def test_default_actor_is_system_so_internal_callers_are_unaffected():
    t = ts.create_task("work")
    updated = ts.update_task(t.id, status=TaskStatus.awaiting_input)  # no actor kwarg
    assert updated.status == TaskStatus.awaiting_input


# -------------------- error shape --------------------

def test_illegal_transition_message_names_from_to_and_actor():
    t = ts.create_task("work")  # todo
    with pytest.raises(IllegalTransition) as excinfo:
        ts.update_task(t.id, status=TaskStatus.done, actor=Actor.agent)
    msg = str(excinfo.value)
    assert "todo" in msg
    assert "done" in msg
    assert "agent" in msg
    assert excinfo.value.from_status == TaskStatus.todo
    assert excinfo.value.to_status == TaskStatus.done
    assert excinfo.value.actor == "agent"


def test_illegal_transition_is_a_value_error():
    # Routes rely on this to turn it into an HTTP 400 via their existing
    # `except ValueError` handling.
    assert issubclass(IllegalTransition, ValueError)


def test_check_transition_accepts_string_actor():
    check_transition(TaskStatus.todo, TaskStatus.ready, "agent")
    with pytest.raises(IllegalTransition):
        check_transition(TaskStatus.todo, TaskStatus.done, "agent")


def test_check_transition_rejects_unknown_actor():
    with pytest.raises(IllegalTransition):
        check_transition(TaskStatus.todo, TaskStatus.ready, "superuser")


# -------------------- block_task / stop_task keep working --------------------

def test_block_task_and_stop_task_default_to_system_actor():
    t = ts.create_task("work", status=TaskStatus.in_progress)
    blocked = ts.block_task(t.id, "reason")
    assert blocked.status == TaskStatus.blocked
    stopped = ts.stop_task(t.id)
    assert stopped.status == TaskStatus.stopped
