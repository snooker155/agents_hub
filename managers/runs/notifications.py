"""
Side effects a run's status transition fans out to: the instance registry, the
user's inbox and the dashboard's live stream.

None of this is storage. It lives apart from :mod:`managers.runs.store` because
every one of these is best-effort — a failed notification must never break the
recording of the run that triggered it — and because the store must stay
readable as pure persistence. The store calls into here at its single
read-modify-write chokepoint, so every execution channel (chat, subprocess,
node worker, container, flow node) gets the same fan-out for free.

This module deliberately imports nothing from its siblings: it is the bottom of
the package's import order, so the store can depend on it without a cycle.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from common import db
from common.session_broker import notify_change, notify_delta


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
        # A node or container outlives the run it just executed: it goes back to
        # standby, not away. Only a one-shot carrier actually finishes.
        if new.get("node_id") or new.get("container_name"):
            ireg.mark_standby(instance_id, "idle — waiting for work")
        elif new_status in ("failed", "error"):
            ireg.mark_failed(instance_id, str(new.get("error") or "run failed"))
        else:
            ireg.mark_finished(instance_id, "run finished")
    except Exception:
        # Instance bookkeeping must never break run recording.
        pass


_NOTIFY_TERMINAL_STATUSES = {"completed", "failed"}


# The orchestrator only routes work; its runs never announce a task start.
_ROUTING_AGENT_ID = "orchestrator"


def _notify_task_run_finished(old: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Push an inbox notification when a user task's agent run reaches a terminal state.

    Every run-finalization path (subprocess close_run, node-mode workers, flow
    drivers) funnels through _update_run, so this single hook covers all
    execution modes without relying on the LLM to report completion. Scope is
    deliberately narrow: only task-bound runs (chat/delegation runs are
    excluded — the user is watching those), only user/external-created tasks
    (skips internal orchestrator-created tasks), and never the orchestrator's
    own routing runs. Stopped runs are skipped too: the user stopped them.
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
        if not task_id or agent_id == _ROUTING_AGENT_ID:
            return

        from uuid import UUID as _UUID
        from tasks import service as _ts
        task = _ts.get_task(_UUID(str(task_id)))
        # created_by is a str-mixin enum, so direct string comparison works.
        if not task or getattr(task, "created_by", None) not in ("user", "external"):
            return

        from plans import service as _plan_service
        ok = str(new.get("status")) == "completed"
        body = (
            f"Agent '{agent_id}' finished the task."
            if ok
            else f"Agent '{agent_id}' failed: {new.get('error') or 'unknown error'}"
        )
        _plan_service.create_notification(
            title=f"Task {'completed' if ok else 'failed'}: {task.title}",
            body=body,
            severity="success" if ok else "error",
            source={"task_id": str(task_id), "run_id": str(new.get("run_id") or "")},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:
        # Notification delivery must never break run bookkeeping.
        pass


def _task_already_announced(task_id: str, run_id: str) -> bool:
    """True if an earlier executor run already announced this task's start.

    A task is re-run many times over its life: the orchestrator re-routes it,
    the executor retries, a reviewer picks it up. Each of those calls open_run,
    and every one of them used to emit its own "Task started: <title>" entry —
    identical text, fresh unread row, so the inbox looked like read messages
    were lighting up again. Announce a task once, on its first executor run.
    """
    try:
        conn = db.get_conn()
        row = conn.execute(
            "SELECT 1 FROM runs WHERE task_id = ? AND run_id != ? "
            "AND COALESCE(agent_id, '') != ? LIMIT 1",
            (str(task_id), str(run_id), _ROUTING_AGENT_ID),
        ).fetchone()
        return row is not None
    except Exception:
        # Can't tell — stay quiet rather than risk another duplicate.
        return True


def _notify_task_run_started(record: Dict[str, Any]) -> None:
    """Push an inbox notification when a task's agent run begins executing.

    Called from open_run on the run→running transition. Fires for every task
    regardless of who created it — including orchestrator-created/delegated
    tasks — but only ONCE per task, on the first run by an actual executor.
    The orchestrator's own routing run is skipped (it announces nothing the
    user did not already trigger), and so is every later re-route, retry and
    review run, which is what `_task_already_announced` checks.
    """
    try:
        if str(record.get("status") or "") != "running":
            return
        task_id = record.get("task_id")
        agent_id = str(record.get("agent_id") or "")
        if not task_id or agent_id == _ROUTING_AGENT_ID:
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
            source={"task_id": str(task_id), "run_id": str(record.get("run_id") or "")},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:
        # Notification delivery must never break run bookkeeping.
        pass


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
