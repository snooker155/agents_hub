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


def _park_approval(tool="run_shell", *, hours_ago=3.0, agent_id="swe_agent",
                    reason="on the approval list"):
    """Create a task parked in awaiting_approval with an old asked_at."""
    t = ts.create_task("work")
    asked_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    ts.update_task(t.id, status=TaskStatus.awaiting_approval, pending_approval={
        "tool": tool, "input": {"command": "rm -rf build"}, "reason": reason,
        "run_id": "r-1", "agent_id": agent_id, "hook": "", "fingerprint": "fp-1",
        "asked_at": asked_at,
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


# -------------------- awaiting_approval: reminders, never auto-answer --------------------

def test_approval_reminder_after_threshold_and_no_double_remind(monkeypatch):
    _cfg(monkeypatch, awaiting_input_reminder_hours=2)
    notifs = []
    monkeypatch.setattr("plans.service.create_notification", lambda **k: notifs.append(k))
    task = _park_approval(tool="run_shell", hours_ago=3)

    res = escalation.sweep_awaiting_input()
    assert res["approval_reminded"] == 1
    assert len(notifs) == 1
    assert "run_shell" in notifs[0]["body"]
    assert notifs[0]["title"] == "A tool call is still waiting for your approval"

    # last_reminded_at is now set → immediate re-sweep does not re-remind.
    res2 = escalation.sweep_awaiting_input()
    assert res2["approval_reminded"] == 0
    assert len(notifs) == 1

    refreshed = ts.get_task(task.id)
    assert refreshed.pending_approval.get("reminder_count") == 1
    assert refreshed.pending_approval.get("last_reminded_at")
    # A reminder never resolves the approval: the task stays parked exactly
    # as it was, waiting for a human decision.
    assert refreshed.status == TaskStatus.awaiting_approval
    assert refreshed.pending_approval.get("tool") == "run_shell"


def test_no_approval_reminder_before_threshold(monkeypatch):
    _cfg(monkeypatch, awaiting_input_reminder_hours=5)
    monkeypatch.setattr("plans.service.create_notification", lambda **k: None)
    _park_approval(hours_ago=1)
    assert escalation.sweep_awaiting_input()["approval_reminded"] == 0


def test_an_approval_is_never_auto_answered_however_long_it_waits(monkeypatch, no_launch):
    """Auto-answer is an awaiting_input-only feature: an unapproved tool call
    is never made on the operator's behalf, no matter how long it waits or
    how the auto-answer settings are configured — the approval is only ever
    reminded, and the reminder never touches approved_calls or resumes the run."""
    _cfg(monkeypatch, awaiting_input_reminder_hours=2,
         awaiting_input_auto_answer=True,
         awaiting_input_auto_answer_hours=1, awaiting_input_default_answer="yes")
    monkeypatch.setattr("agents.registry.get_agent", lambda a: object())
    monkeypatch.setattr("plans.service.create_notification", lambda **k: None)
    task = _park_approval(hours_ago=10)

    res = escalation.sweep_awaiting_input()
    assert res["auto_answered"] == 0
    assert res["approval_reminded"] == 1
    assert no_launch == []

    refreshed = ts.get_task(task.id)
    assert refreshed.status == TaskStatus.awaiting_approval
    assert refreshed.pending_approval is not None
    assert refreshed.approved_calls == []
