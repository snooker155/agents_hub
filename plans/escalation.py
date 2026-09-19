"""
Awaiting-input escalation sweep.

Tasks parked by ``ask_user`` (status ``awaiting_input`` with a ``pending_question``)
otherwise wait forever for a human. This periodic sweep, ticked by the plan
scheduler, does two opt-in things:

1. **Reminders** — once a parked task is older than ``awaiting_input_reminder_hours``
   it emits an inbox notification, then re-reminds at the same cadence (tracked
   via ``last_reminded_at`` on the pending question so it never spams every tick).
2. **Auto-answer** — when ``awaiting_input_auto_answer`` is enabled and a task has
   waited past ``awaiting_input_auto_answer_hours``, it resumes the task with the
   configured default answer. Deliberately conservative: it skips questions that
   look destructive, and it is off by default.

Everything is gated on config (all thresholds default to disabled) and fails
soft — a bad task must never wedge the sweep for the others.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger("plans.escalation")

# Questions whose auto-answering could do real damage are never auto-answered —
# they always fall through to a reminder and wait for a human.
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|drop|remove|destroy|wipe|overwrite|force[- ]?push|reset|"
    r"terminate|shut ?down|purge|revoke|deploy|production|irreversible)\b",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def _looks_destructive(question: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(question or ""))


def _emit_reminder(task, pending: Dict[str, Any], age_hours: float) -> None:
    from plans import service as _plan_service

    q = str(pending.get("question") or "").strip()
    _plan_service.create_notification(
        title="A task is still waiting for your input",
        body=(q or "The agent is waiting for your answer.")
        + f"\n\n(Waiting {age_hours:.0f}h — task {getattr(task, 'key', '') or str(task.id)}.)",
        severity="warning",
        source={"origin": "escalation", "task_id": str(task.id)},
        workspace=str(getattr(task, "workspace", "") or "") or None,
        channels=["dashboard"],
    )


def _auto_answer(task, pending: Dict[str, Any], answer: str) -> bool:
    """Resume a parked task with ``answer`` (mirrors the /answer route core).
    Returns True on a successful re-dispatch."""
    from tasks import service as ts
    from agents.registry import get_agent
    from agents import agent_launcher

    agent_id = pending.get("agent_id") or getattr(task, "assigned_agent_type", None)
    if not agent_id or not get_agent(agent_id):
        return False

    question = str(pending.get("question") or "").strip()
    resume_desc = (
        f'You previously paused this task to ask the user:\n"{question}"\n\n'
        f'No human responded in time, so a default answer was applied automatically:\n"{answer}"\n\n'
        "Continue the task using this answer. Do not ask the same question again."
    )
    params = {"description": resume_desc}
    try:
        run_id, _session_id = agent_launcher.start_run(str(task.id), agent_id, params)
        ts.assign_agent(task.id, agent_id, params, run_id=run_id)
        ts.update_task(task.id, status=ts.TaskStatus.in_progress, pending_question=None)
        try:
            from common.session_service import rebind_continuations_to_run
            rebind_continuations_to_run(str(task.id), run_id)
        except Exception:
            pass
        ts.append_task_activity_log(
            task.id, "auto_answer",
            f"No human response — auto-answered with default: {answer}",
            run_id=run_id, agent_id=agent_id,
        )
        try:
            from plans import service as _plan_service
            _plan_service.create_notification(
                title="A waiting task was auto-answered",
                body=f'"{question}"\n\nAuto-answered with the default: "{answer}".',
                severity="warning",
                source={"origin": "escalation", "task_id": str(task.id)},
                workspace=str(getattr(task, "workspace", "") or "") or None,
                channels=["dashboard"],
            )
        except Exception:
            pass
        return True
    except Exception:
        log.exception("auto-answer failed for task %s", task.id)
        return False


def sweep_awaiting_input() -> Dict[str, int]:
    """One escalation pass over all ``awaiting_input`` tasks. Returns a summary
    ``{reminded, auto_answered}``. No-ops (``{"skipped": 1}``) when nothing is
    enabled in config."""
    from common.config import settings

    reminder_hours = int(getattr(settings, "awaiting_input_reminder_hours", 0) or 0)
    auto_enabled = bool(getattr(settings, "awaiting_input_auto_answer", False))
    auto_hours = int(getattr(settings, "awaiting_input_auto_answer_hours", 0) or 0)
    default_answer = str(getattr(settings, "awaiting_input_default_answer", "") or "").strip()
    auto_ready = auto_enabled and auto_hours > 0 and bool(default_answer)

    if reminder_hours <= 0 and not auto_ready:
        return {"skipped": 1}

    from tasks import service as ts

    now = _now()
    reminded = 0
    auto_answered = 0

    try:
        tasks = ts.list_tasks()
    except Exception:
        return {"skipped": 1}

    for task in tasks:
        try:
            if str(getattr(task, "status", "")) != str(ts.TaskStatus.awaiting_input):
                continue
            pending = getattr(task, "pending_question", None) or {}
            asked_at = _parse(pending.get("asked_at"))
            if not asked_at:
                continue
            age_hours = (now - asked_at).total_seconds() / 3600.0

            # Auto-answer takes priority once its (longer) threshold is crossed.
            if auto_ready and age_hours >= auto_hours:
                question = str(pending.get("question") or "")
                if not _looks_destructive(question):
                    if _auto_answer(task, pending, default_answer):
                        auto_answered += 1
                    continue
                # Destructive → never auto-answer; fall through to reminders.

            if reminder_hours > 0 and age_hours >= reminder_hours:
                last_reminded = _parse(pending.get("last_reminded_at"))
                since_last = (now - last_reminded).total_seconds() / 3600.0 if last_reminded else None
                if since_last is None or since_last >= reminder_hours:
                    _emit_reminder(task, pending, age_hours)
                    updated = dict(pending)
                    updated["last_reminded_at"] = now.isoformat()
                    updated["reminder_count"] = int(pending.get("reminder_count") or 0) + 1
                    ts.update_task(task.id, pending_question=updated)
                    reminded += 1
        except Exception:
            log.exception("escalation sweep failed for a task")
            continue

    if reminded or auto_answered:
        log.info("awaiting-input sweep: reminded %d, auto-answered %d", reminded, auto_answered)
    return {"reminded": reminded, "auto_answered": auto_answered}


__all__ = ["sweep_awaiting_input"]
