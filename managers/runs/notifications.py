"""
Side effects a run's status transition fans out to: the instance registry, the
user's inbox and the dashboard's live stream.

None of this is storage. It lives apart from :mod:`managers.runs.store` because
every one of these is best-effort — a failed notification must never break the
recording of the run that triggered it — and because the store must stay
readable as pure persistence. The store calls into here at its single
read-modify-write chokepoint, so every execution channel (chat, subprocess,
resident instance, container, flow node) gets the same fan-out for free.

This module deliberately imports nothing from its siblings: it is the bottom of
the package's import order, so the store can depend on it without a cycle.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from common import db
from common.session_broker import notify_change, notify_delta

log = logging.getLogger(__name__)


# -------------------- Instance bookkeeping --------------------
# A run is one unit of work; the *instance* is the agent copy that performed it.
# Every execution channel eventually writes its run through _apply(), so hooking
# the status transition here keeps instance state correct for chat, subprocess,
# node, container and flow runs without each of them remembering to do it.

_TERMINAL_RUN_STATUSES = ("completed", "stopped", "failed", "error")


def _sync_instance(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> None:
    """Move the run's instance along with the run's status transition."""
    instance_id = str(new.get("instance_id") or "")
    if not instance_id:
        return
    old_status = str((old or {}).get("status") or "")
    new_status = str(new.get("status") or "")
    if old_status == new_status:
        return
    try:
        from instances import registry as ireg
        from instances import store as istore

        if new_status == "running":
            hint = new.get("title") or new.get("input") or ""
            ireg.mark_active(instance_id, str(new.get("run_id") or ""), str(hint)[:200] or None)
            return
        if new_status not in _TERMINAL_RUN_STATUSES:
            return

        proc = new.get("process") or {}
        usage = (proc.get("token_usage") or {}) if isinstance(proc, dict) else {}
        istore.add_run_stats(
            instance_id,
            tokens=int(usage.get("total_tokens") or 0),
            duration_ms=int((proc or {}).get("duration_ms") or 0),
        )
        # A resident instance's carrier (and a node before it) outlives the run
        # it just executed: it goes back to standby, not away. A one-shot
        # carrier, a task run's own process or container, actually finishes.
        # A carrier running several conversations marks itself active again if
        # another run is still going.
        if new.get("carrier_run") or new.get("node_id"):
            ireg.mark_standby(instance_id, "idle — waiting for work")
        elif new_status in ("failed", "error"):
            ireg.mark_failed(instance_id, str(new.get("error") or "run failed"))
        else:
            ireg.mark_finished(instance_id, "run finished")
    except Exception:  # noqa: BLE001 - instance bookkeeping must never break run recording
        log.debug("instance sync failed for %s", instance_id, exc_info=True)


_NOTIFY_TERMINAL_STATUSES = {"completed", "failed"}


def notification_message(key: str, **params: Any) -> Dict[str, Any]:
    """What a notification says, as a message key and its values, kept in
    its ``source``. The backend does not know the reader's language, so the
    dashboard builds the title and text from this in theirs
    (components.notifications.messages.<key>); the stored English title and
    body stay as the fallback for a key it does not know."""
    return {"key": key, "params": {k: v for k, v in params.items() if v is not None}}


# The orchestrator only routes work; its runs never announce a task start.
_ROUTING_AGENT_ID = "orchestrator"
# Agents whose runs on a task are not the work itself: routing, splitting it
# into subtasks, grading or reviewing it, planning. They announce nothing,
# and their runs do not count as the task having started.
_SERVICE_AGENT_IDS = frozenset({
    _ROUTING_AGENT_ID, "decomposer", "outcome_grader", "code_reviewer", "planner",
})


def _created_by_a_person(task: Any) -> bool:
    """True when the task, or the top of the tree it belongs to, was created
    by a person (or came in from an external tracker). Subtasks are created by
    the decomposer or the orchestrator, but they are the person's work too."""
    from tasks import service as _ts
    seen = set()
    while task is not None and task.id not in seen:
        seen.add(task.id)
        if getattr(task, "created_by", None) in ("user", "external"):
            return True
        if not task.parent_id:
            return False
        task = _ts.get_task(task.parent_id)
    return False


def _notify_task_run_finished(old: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Push an inbox notification when a user task's agent run reaches a terminal state.

    Every run-finalization path (subprocess close_run, node-mode workers, flow
    drivers) funnels through _update_run, so this single hook covers all
    execution modes without relying on the LLM to report completion. Scope is
    deliberately narrow: only task-bound runs (chat/delegation runs are
    excluded — the user is watching those), only user/external-created tasks
    (skips internal orchestrator-created tasks), and never the orchestrator's
    own successful routing runs: a routing run that fails leaves the task
    with nobody on it, so the person hears about that one. Stopped runs are
    skipped too: the user stopped them.
    """
    try:
        if str(new.get("status") or "") not in _NOTIFY_TERMINAL_STATUSES:
            return
        if str(old.get("status") or "") in _NOTIFY_TERMINAL_STATUSES:
            return  # already finalized — don't notify twice

        # Alert rules (run_failed, spend thresholds) evaluate here too, for
        # every run reaching a terminal status — not only task-bound ones.
        from notify import rules as notify_rules
        notify_rules.evaluate_run_finished(new)

        task_id = new.get("task_id")
        agent_id = str(new.get("agent_id") or "")
        ok = str(new.get("status")) == "completed"
        routing_failed = agent_id == _ROUTING_AGENT_ID and not ok
        if not task_id or (agent_id in _SERVICE_AGENT_IDS and not routing_failed):
            return

        from uuid import UUID as _UUID
        from tasks import service as _ts
        task = _ts.get_task(_UUID(str(task_id)))
        if not task or not _created_by_a_person(task):
            return

        from plans import service as _plan_service
        if routing_failed:
            _plan_service.create_notification(
                title=f"Routing failed: {task.title}",
                body=f"The orchestrator could not route the task: {new.get('error') or 'unknown error'}",
                severity="error",
                source={"task_id": str(task_id), "run_id": str(new.get("run_id") or ""),
                        "message": notification_message(
                            "routingFailed", title=task.title,
                            error=str(new.get("error") or "") or None)},
                workspace=getattr(task, "workspace", None),
            )
            return
        body = (
            f"Agent '{agent_id}' finished the task."
            if ok
            else f"Agent '{agent_id}' failed: {new.get('error') or 'unknown error'}"
        )
        _plan_service.create_notification(
            title=f"Task {'completed' if ok else 'failed'}: {task.title}",
            body=body,
            severity="success" if ok else "error",
            source={"task_id": str(task_id), "run_id": str(new.get("run_id") or ""),
                    "message": notification_message(
                        "taskCompleted" if ok else "taskFailed", title=task.title, agent=agent_id,
                        error=None if ok else (str(new.get("error") or "") or None))},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:  # noqa: BLE001 - notification delivery must never break run bookkeeping
        log.debug("_notify_task_run_finished failed for task %s", new.get("task_id"), exc_info=True)


def _attempt_started_at(task_id: str) -> str:
    """When the task last went back to todo or ready (its activity log), as
    an ISO time; "" when it never did. A person re-running a task resets it
    first, so this is where its current attempt begins."""
    try:
        from uuid import UUID as _UUID
        from tasks import service as _ts
        for entry in reversed(_ts.get_task_activity_log(_UUID(str(task_id))) or []):
            if entry.get("type") != "status_change":
                continue
            # Older entries name the enum ("TaskStatus.todo"), newer the value.
            if str(entry.get("to") or "").rsplit(".", 1)[-1] in ("todo", "ready"):
                return str(entry.get("timestamp") or "")
    except Exception:  # noqa: BLE001 - no log means the attempt is the task's whole life
        log.debug("activity log read failed for task %s", task_id, exc_info=True)
    return ""


def _task_already_announced(task_id: str, run_id: str) -> bool:
    """True if an earlier executor run already announced this attempt's start.

    A task is re-run many times within one attempt: the orchestrator re-routes
    it, the executor retries, a reviewer picks it up. Each of those calls
    open_run, and every one of them used to emit its own "Task started:
    <title>" entry — identical text, fresh unread row, so the inbox looked
    like read messages were lighting up again. Announce an attempt once, on
    its first executor run. A task reset to todo or ready and run again is a
    new attempt, and the person who started it hears about it.
    """
    try:
        since = _attempt_started_at(task_id)
        placeholders = ", ".join("?" for _ in _SERVICE_AGENT_IDS)
        conn = db.get_conn()
        row = conn.execute(
            "SELECT 1 FROM runs WHERE task_id = ? AND run_id != ? "
            f"AND COALESCE(agent_id, '') NOT IN ({placeholders}) "
            "AND COALESCE(started_at, created_at, '') >= ? LIMIT 1",
            (str(task_id), str(run_id), *sorted(_SERVICE_AGENT_IDS), since),
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 - can't tell, stay quiet rather than risk another duplicate
        log.debug("_task_already_announced failed for task %s", task_id, exc_info=True)
        return True


def _notify_task_run_started(record: Dict[str, Any]) -> None:
    """Push an inbox notification when a task's agent run begins executing.

    Called from open_run on the run→running transition. Fires for every task
    regardless of who created it — including orchestrator-created/delegated
    tasks — but only ONCE per attempt, on the first run by an actual executor.
    The service agents' runs are skipped (routing, splitting, grading and
    review announce nothing the user did not already trigger), and so is
    every later re-route and retry run, which is what
    `_task_already_announced` checks.
    """
    try:
        if str(record.get("status") or "") != "running":
            return
        task_id = record.get("task_id")
        agent_id = str(record.get("agent_id") or "")
        if not task_id or agent_id in _SERVICE_AGENT_IDS:
            return
        if _task_already_announced(str(task_id), str(record.get("run_id") or "")):
            return

        from uuid import UUID as _UUID
        from tasks import service as _ts
        task = _ts.get_task(_UUID(str(task_id)))
        if not task:
            return

        from plans import service as _plan_service
        _plan_service.create_notification(
            title=f"Task started: {task.title}",
            body=f"Agent '{agent_id}' started the task.",
            severity="info",
            source={"task_id": str(task_id), "run_id": str(record.get("run_id") or ""),
                    "message": notification_message("taskStarted", title=task.title, agent=agent_id)},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:  # noqa: BLE001 - notification delivery must never break run bookkeeping
        log.debug("_notify_task_run_started failed for task %s", record.get("task_id"), exc_info=True)


def notify_assignment_awaiting_approval(task: Any, agent_id: str,
                                        reason: Optional[str] = None) -> None:
    """Ask the person to approve the agent the orchestrator chose for a task.

    In manual assignment mode nothing runs until someone approves, so the
    request has to reach the inbox: otherwise the task just waits unseen.
    A person assigning in the UI approves on the spot and is not asked.
    """
    try:
        from plans import service as _plan_service
        why = (reason or "").strip()
        body = f"'{agent_id}' was chosen for the task" + (f": {why}" if why else ".")
        _plan_service.create_notification(
            title=f"Approval needed: {task.title}",
            body=body + " Approve it on the task page to start it.",
            severity="warning",
            source={"task_id": str(task.id), "agent_id": agent_id, "kind": "assignment_approval",
                    "message": notification_message("approvalNeeded", title=task.title,
                                                    agent=agent_id, reason=why or None)},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:  # noqa: BLE001 - notification delivery must never break the assignment
        log.debug("approval notification failed for task %s", getattr(task, "id", None), exc_info=True)


def notify_container_completed(task: Any, subtasks: int) -> None:
    """Tell the person a task whose subtasks were its work is now done.

    Such a parent completes by a status change, not by a run of its own, so
    the run hooks above never see it finish.
    """
    try:
        if not _created_by_a_person(task):
            return
        from plans import service as _plan_service
        _plan_service.create_notification(
            title=f"Task completed: {task.title}",
            body=f"All {subtasks} subtasks are done.",
            severity="success",
            source={"task_id": str(task.id),
                    "message": notification_message("containerCompleted", title=task.title,
                                                    count=int(subtasks))},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:  # noqa: BLE001 - notification delivery must never break the status change
        log.debug("notify_container_completed failed for task %s", getattr(task, "id", None), exc_info=True)


# Fields a list row needs to repaint itself. A run update publishes these as a
# delta so an open Messages page patches one row, instead of every open tab
# refetching the whole list — which, with a thousand runs in flight, is the
# difference between a live page and a permanently loading one.
_RUN_DELTA_FIELDS = (
    "run_id", "agent_id", "status", "workspace", "session_id", "instance_id",
    "task_id", "title", "started_at", "finished_at", "error", "channel",
    "model", "provider", "total_tokens", "duration_ms",
)


def _publish_run_delta(record: Optional[Dict[str, Any]], run_id: str) -> None:
    if not record:
        notify_change("runs", run_id=str(run_id))
        return
    proc = record.get("process") or {}
    usage = (proc.get("token_usage") or {}) if isinstance(proc, dict) else {}
    delta = {k: record.get(k) for k in _RUN_DELTA_FIELDS}
    delta["run_id"] = str(run_id)
    delta["total_tokens"] = usage.get("total_tokens", record.get("total_tokens"))
    delta["duration_ms"] = (proc or {}).get("duration_ms", record.get("duration_ms"))
    notify_delta("runs", str(run_id), delta)
