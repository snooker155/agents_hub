"""The fix->review cycle limit: _auto_start_review counts review starts on the
task and blocks it for the user once the limit is reached, instead of looping
forever between a fix and a reviewer rejection (see managers.runs.task_finalize).

Driven directly at the same level as tests/test_finalize.py — _auto_start_review
is the choke point every continuation-mode review start goes through, whether
it is the first pass or a retry after a reviewer rejection.
"""
from types import SimpleNamespace

import pytest

from managers.runs import task_finalize as tf
from tasks import service as ts
from tasks.models import TaskStatus


@pytest.fixture
def reviewer_available(monkeypatch):
    """Make agents.registry.get_agent("code_reviewer") resolve, without
    depending on the bootstrap seed being loaded in the test's state root."""
    import agents.registry as registry

    def _get_agent(agent_id):
        return SimpleNamespace(id=agent_id) if agent_id == "code_reviewer" else None

    monkeypatch.setattr(registry, "get_agent", _get_agent)


def test_auto_start_review_increments_review_cycles(reviewer_available, no_launch):
    t = ts.create_task("work", status=TaskStatus.resolved)

    started = tf._auto_start_review(t.id, ts.get_task(t.id))

    assert started is True
    updated = ts.get_task(t.id)
    assert updated.status == TaskStatus.reviewing
    assert updated.review_cycles == 1
    assert no_launch == [(str(t.id), "code_reviewer")]


def test_repeated_fix_review_passes_keep_counting(reviewer_available, no_launch):
    t = ts.create_task("work", status=TaskStatus.resolved)

    tf._auto_start_review(t.id, ts.get_task(t.id))
    # A fix cycle would move the task through in_progress/resolved again; only
    # the count on the task matters here, so drive it back to resolved by hand.
    ts.update_task(t.id, status=TaskStatus.in_progress)
    ts.update_task(t.id, status=TaskStatus.resolved)
    tf._auto_start_review(t.id, ts.get_task(t.id))

    assert ts.get_task(t.id).review_cycles == 2


def test_limit_reached_blocks_instead_of_reviewing_again(reviewer_available, no_launch):
    t = ts.create_task("work", status=TaskStatus.resolved)
    ts.update_task(t.id, review_cycles=tf.MAX_REVIEW_CYCLES)

    started = tf._auto_start_review(t.id, ts.get_task(t.id))

    assert started is False
    blocked = ts.get_task(t.id)
    assert blocked.status == TaskStatus.blocked
    assert str(tf.MAX_REVIEW_CYCLES) in (blocked.blocked_reason or "")
    # No review run was launched once the limit blocked it.
    assert no_launch == []


def test_limit_reached_notifies_the_user(reviewer_available, no_launch, monkeypatch):
    notifications = []
    from plans import service as plan_service

    monkeypatch.setattr(plan_service, "create_notification",
                         lambda **kwargs: notifications.append(kwargs))

    t = ts.create_task("work", status=TaskStatus.resolved)
    ts.update_task(t.id, review_cycles=tf.MAX_REVIEW_CYCLES)

    tf._auto_start_review(t.id, ts.get_task(t.id))

    assert len(notifications) == 1
    assert "review cycle limit" in notifications[0]["title"].lower()
