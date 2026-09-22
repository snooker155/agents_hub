"""TaskPriority: coercion, dispatch ordering, and the overdue flag."""
from datetime import datetime, timedelta, timezone

from tasks import service as ts
from tasks.models import Task, TaskPriority, TaskStatus, coerce_priority, is_overdue, priority_sort_key


# -------------------- enum coercion --------------------

def test_default_priority_is_medium():
    t = ts.create_task("work")
    assert t.priority == TaskPriority.medium


def test_priority_accepts_known_strings_case_insensitively():
    for raw, expected in [("low", TaskPriority.low), ("HIGH", TaskPriority.high), ("Critical", TaskPriority.critical)]:
        t = Task(title="x", priority=raw)
        assert t.priority == expected


def test_unknown_priority_string_falls_back_to_medium():
    t = Task(title="x", priority="urgent-ish")
    assert t.priority == TaskPriority.medium


def test_none_priority_falls_back_to_medium():
    assert coerce_priority(None) == TaskPriority.medium
    t = Task(title="x", priority=None)
    assert t.priority == TaskPriority.medium


def test_coerce_priority_passes_through_enum_values():
    assert coerce_priority(TaskPriority.critical) is TaskPriority.critical


def test_priority_sort_key_orders_critical_first():
    keys = [priority_sort_key(p) for p in (TaskPriority.low, TaskPriority.medium, TaskPriority.high, TaskPriority.critical)]
    assert keys == sorted(keys, reverse=True)
    assert priority_sort_key(TaskPriority.critical) < priority_sort_key(TaskPriority.low)


def test_update_task_coerces_an_invalid_priority_string():
    t = ts.create_task("work")
    updated = ts.update_task(t.id, priority="not-a-priority")
    assert updated.priority == TaskPriority.medium


# -------------------- dispatch ordering --------------------

def test_order_for_dispatch_sorts_by_priority_first():
    now = datetime.now(timezone.utc)
    a = Task(title="a", priority="low", created_at=now)
    b = Task(title="b", priority="critical", created_at=now)
    c = Task(title="c", priority="medium", created_at=now)
    ordered = ts.order_for_dispatch([a, b, c])
    assert [t.title for t in ordered] == ["b", "c", "a"]


def test_order_for_dispatch_breaks_priority_ties_by_due_at_then_created_at():
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=1)
    later = now + timedelta(days=1)
    no_due = Task(title="no_due", priority="high", created_at=now)
    due_later = Task(title="due_later", priority="high", created_at=now, due_at=later)
    due_soon = Task(title="due_soon", priority="high", created_at=now, due_at=soon)
    ordered = ts.order_for_dispatch([no_due, due_later, due_soon])
    # Same priority: due_at ascending, no-deadline tasks sorted last.
    assert [t.title for t in ordered] == ["due_soon", "due_later", "no_due"]


def test_order_for_dispatch_breaks_full_ties_by_created_at_oldest_first():
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(minutes=5)
    older = Task(title="older", priority="medium", created_at=t0)
    newer = Task(title="newer", priority="medium", created_at=t1)
    ordered = ts.order_for_dispatch([newer, older])
    assert [t.title for t in ordered] == ["older", "newer"]


def test_order_for_dispatch_combines_priority_and_deadline_correctly():
    now = datetime.now(timezone.utc)
    critical_no_due = Task(title="critical_no_due", priority="critical", created_at=now)
    high_due_soon = Task(title="high_due_soon", priority="high", created_at=now, due_at=now + timedelta(hours=1))
    # Priority still wins over an earlier deadline at a lower priority.
    ordered = ts.order_for_dispatch([high_due_soon, critical_no_due])
    assert [t.title for t in ordered] == ["critical_no_due", "high_due_soon"]


# -------------------- overdue flag --------------------

def test_is_overdue_true_for_past_due_active_task():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert is_overdue(past, TaskStatus.in_progress) is True


def test_is_overdue_false_without_a_deadline():
    assert is_overdue(None, TaskStatus.in_progress) is False


def test_is_overdue_false_for_future_deadline():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert is_overdue(future, TaskStatus.in_progress) is False


def test_is_overdue_false_for_terminal_statuses_even_if_past_due():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    for status in (TaskStatus.done, TaskStatus.reviewed, TaskStatus.resolved, TaskStatus.stopped):
        assert is_overdue(past, status) is False


def test_is_overdue_assumes_utc_for_naive_datetimes():
    naive_past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    assert is_overdue(naive_past, TaskStatus.todo) is True


def test_task_overdue_property_reflects_due_at_and_status():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    t = ts.create_task("work")
    t = ts.update_task(t.id, due_at=past)
    assert t.overdue is True
    done = ts.update_task(t.id, status=TaskStatus.done)
    assert done.overdue is False


def test_due_at_naive_input_is_assumed_utc():
    naive = datetime(2030, 1, 1, 12, 0, 0)
    t = Task(title="x", due_at=naive)
    assert t.due_at.tzinfo is not None
