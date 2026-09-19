"""
Awaiting-input escalation sweep: reminders + opt-in auto-answer for tasks
parked by ``ask_user``.
"""
from datetime import datetime, timedelta, timezone

from plans import escalation
from tasks import service as ts
from tasks.models import TaskStatus


def _park(question="Which option?", *, hours_ago=3.0, agent_id="swe_agent"):
    """Create a task parked in awaiting_input with an old asked_at."""
    t = ts.create_task("work")
    asked_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    ts.update_task(t.id, status=TaskStatus.awaiting_input, pending_question={
        "question": question, "asked_at": asked_at, "agent_id": agent_id,
    })
    return ts.get_task(t.id)


def _cfg(monkeypatch, **kw):
    from common.config import settings
    defaults = {
        "awaiting_input_reminder_hours": 0,
        "awaiting_input_auto_answer": False,
        "awaiting_input_auto_answer_hours": 0,
        "awaiting_input_default_answer": "",
    }
    for k, v in {**defaults, **kw}.items():
        monkeypatch.setattr(settings, k, v)


def test_disabled_is_noop(monkeypatch):
    _cfg(monkeypatch)  # everything off
    _park()
    assert escalation.sweep_awaiting_input() == {"skipped": 1}


def test_reminder_after_threshold_and_no_double_remind(monkeypatch):
    _cfg(monkeypatch, awaiting_input_reminder_hours=2)
    notifs = []
    monkeypatch.setattr("plans.service.create_notification", lambda **k: notifs.append(k))
    task = _park(hours_ago=3)

    res = escalation.sweep_awaiting_input()
    assert res["reminded"] == 1
    assert len(notifs) == 1

    # last_reminded_at is now set → immediate re-sweep does not re-remind.
    res2 = escalation.sweep_awaiting_input()
    assert res2["reminded"] == 0
    assert len(notifs) == 1

    refreshed = ts.get_task(task.id)
    assert refreshed.pending_question.get("reminder_count") == 1
    assert refreshed.pending_question.get("last_reminded_at")


def test_no_reminder_before_threshold(monkeypatch):
    _cfg(monkeypatch, awaiting_input_reminder_hours=5)
    monkeypatch.setattr("plans.service.create_notification", lambda **k: None)
    _park(hours_ago=1)
    assert escalation.sweep_awaiting_input()["reminded"] == 0


def test_auto_answer_resumes_task(monkeypatch, no_launch):
    _cfg(monkeypatch, awaiting_input_auto_answer=True,
         awaiting_input_auto_answer_hours=4, awaiting_input_default_answer="yes, proceed")
    monkeypatch.setattr("agents.registry.get_agent", lambda a: object())
    monkeypatch.setattr("plans.service.create_notification", lambda **k: None)
    task = _park(question="Should I continue?", hours_ago=5)

    res = escalation.sweep_awaiting_input()
    assert res["auto_answered"] == 1

    refreshed = ts.get_task(task.id)
    assert refreshed.status == TaskStatus.in_progress
    assert refreshed.pending_question is None
    assert no_launch == [(str(task.id), "swe_agent")]


def test_auto_answer_skips_destructive_question(monkeypatch, no_launch):
    _cfg(monkeypatch, awaiting_input_auto_answer=True,
         awaiting_input_auto_answer_hours=4, awaiting_input_default_answer="yes")
    monkeypatch.setattr("agents.registry.get_agent", lambda a: object())
    monkeypatch.setattr("plans.service.create_notification", lambda **k: None)
    task = _park(question="Delete the production database?", hours_ago=6)

    res = escalation.sweep_awaiting_input()
    assert res["auto_answered"] == 0
    assert no_launch == []
    assert ts.get_task(task.id).status == TaskStatus.awaiting_input


def test_auto_answer_before_threshold_waits(monkeypatch, no_launch):
    _cfg(monkeypatch, awaiting_input_auto_answer=True,
         awaiting_input_auto_answer_hours=8, awaiting_input_default_answer="ok")
    monkeypatch.setattr("agents.registry.get_agent", lambda a: object())
    _park(hours_ago=2)
    res = escalation.sweep_awaiting_input()
    assert res["auto_answered"] == 0
    assert no_launch == []
