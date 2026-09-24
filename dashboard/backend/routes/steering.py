"""
Routes: steering a running run (docs/steering.md).

``POST /api/runs/{run_id}/steer`` takes a message for a run that is working
right now, in one of two modes:

- ``inject``: stored (``common.steering``) and taken by the agent loop before
  its next model call (``agents/loop_ext/steering.py``). The run keeps going.
- ``interrupt``: stored, then the run is stopped through the same path the
  Stop buttons use (``managers.run_manager.stop_run_by_id``). A task run is
  then launched again on the same task and agent, with the message in its
  instruction; a chat turn is answered with ``{"next": "send"}`` and the
  client sends the message as its next turn itself, since it owns the
  conversation's history.

``GET /api/runs/{run_id}/steer`` lists what was sent to a run and where each
message is (pending, delivered at step N, expired, interrupted). A message
the run never took before it finished is marked expired on the first read
after the run ended.

Access follows the other single-run routes: the caller must see the run's
workspace, and, being a write, steering in ``multi`` mode also needs an
editor's role there. Every write lands in the audit log as ``run.steer``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity, steering
from managers import run_manager

log = logging.getLogger(__name__)

router = APIRouter(tags=["steering"])

#: Statuses of a run that has not finished yet. Only ``running`` accepts a
#: message; the others are reported back in the 409.
_LIVE_STATUSES = ("running", "pending", "queued", "stop")


class SteerBody(BaseModel):
    message: str
    mode: str = steering.MODE_INJECT


def _require_can_steer(request: Request, run: Dict[str, Any]):
    """The caller's principal, once they may write to this run.

    Visible like any single-run route (``access.require_visible``), and in
    ``multi`` mode at least an editor of the run's workspace: steering changes
    what a run does, which a viewer may not.
    """
    from common.auth import MULTI, WS_EDITOR, role_satisfies

    principal = identity.request_principal(request)
    workspace = run.get("workspace")
    access.require_visible(principal, workspace)
    if (workspace and identity.current_mode() == MULTI and principal is not None
            and not principal.is_admin and principal.kind == "user"):
        role = identity.membership_role(str(workspace), principal.id)
        if not role_satisfies(role, WS_EDITOR):
            raise HTTPException(status_code=403,
                                detail="steering a run needs an editor's role in its workspace")
    return principal


def _run_or_404(run_id: str) -> Dict[str, Any]:
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _task_of(run: Dict[str, Any]):
    """The task a run works on, or None. A chat turn's ``task_id`` is its
    conversation id, not a task, so chat runs never have one here."""
    if str(run.get("session_type") or "") == "chat":
        return None
    task_id = run.get("task_id")
    if not task_id:
        return None
    try:
        from tasks import service as tasks_service
        return tasks_service.get_task(UUID(str(task_id)))
    except (ValueError, TypeError):
        return None


def _agent_is_remote(agent_id: str) -> bool:
    try:
        from agents import registry
        spec = registry.get_agent(agent_id)
        return bool(spec is not None and spec.is_remote())
    except Exception:  # noqa: BLE001 - an unreadable definition is treated as a standard agent
        return False


def _snapshot_continuations(task_id: str) -> List[Dict[str, Any]]:
    """The task's pending session continuations, read before a stop drops them."""
    try:
        from common import db
        rows = db.get_conn().execute(
            "SELECT doc FROM continuations WHERE task_id = ?", (str(task_id),)).fetchall()
        return [d for d in (db.loads(r["doc"]) for r in rows) if isinstance(d, dict)]
    except Exception:  # noqa: BLE001 - nothing to restore is the plain stop behaviour
        log.debug("steering: continuations snapshot failed for %s", task_id, exc_info=True)
        return []


def _restore_continuations(docs: List[Dict[str, Any]], task_id: str, new_run_id: str) -> None:
    """Put back what the stop dropped, bound to the run that carries on: an
    orchestrator waiting on this task still hears when the work is done."""
    from common.session_service import register_continuation
    for doc in docs:
        try:
            register_continuation(
                str(doc.get("session_id") or ""), str(task_id),
                workspace=doc.get("workspace"),
                agent_id=str(doc.get("agent_id") or "orchestrator"),
                run_id=new_run_id if doc.get("run_id") else None,
            )
        except Exception:  # noqa: BLE001 - one continuation failing must not stop the rest
            log.debug("steering: continuation restore failed for %s", task_id, exc_info=True)


def interrupt_instruction(message: str, author: Optional[str] = None) -> str:
    """The section a relaunched task run reads first: what the person said
    when they stopped the previous run."""
    who = f"The user ({author})" if author else "The user"
    return (
        f"{who} stopped your previous run on this task to tell you:\n"
        f"\"{message.strip()}\"\n\n"
        "Continue the task with this in mind. Work the previous run already did "
        "(files in the workspace, records it created) is still there: check it "
        "before doing anything again."
    )


def _stop(run_id: str) -> bool:
    """Stop the run the way ``POST /api/messages/{id}/stop`` does, including
    settling an in-process chat turn as stopped at once."""
    from datetime import datetime, timezone

    stopped = run_manager.stop_run_by_id(run_id)
    if stopped:
        updated = run_manager.get_run_by_id(run_id) or {}
        if updated.get("status") == "stop" and not updated.get("pid") and not updated.get("container_name"):
            run_manager.update_run(run_id, {
                "status": "stopped",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "stopped by user",
            })
    return stopped


def _relaunch_task(run: Dict[str, Any], task: Any, message: str,
                   author: Optional[str]) -> str:
    """Launch the task's agent again with the message in its instruction.
    Returns the new run id. The previous run's own parameters are kept (the
    specific ask it was started with), minus anything that resumed a paused
    or dead run."""
    from agents import agent_launcher
    from tasks import service as tasks_service
    from tasks.models import TaskStatus

    agent_id = str(run.get("agent_id") or task.assigned_agent_type or "")
    previous = dict(getattr(task, "assigned_agent_params", None) or {})
    for key in ("resume", "resume_checkpoint"):
        previous.pop(key, None)
    section = interrupt_instruction(message, author)
    base = str(previous.get("description") or "").strip()
    params = {**previous, "description": f"{base}\n\n{section}" if base else section}
    new_run_id, _session = agent_launcher.start_run(str(task.id), agent_id, params)
    tasks_service.assign_agent(task.id, agent_id, params, run_id=new_run_id)
    tasks_service.update_task(task.id, status=TaskStatus.in_progress)
    return new_run_id


@router.post("/api/runs/{run_id}/steer")
async def steer_run(run_id: str, body: SteerBody, request: Request):
    """Send a message to a running run: ``inject`` it before the next model
    step, or ``interrupt`` the run and carry on with it (see module docstring).
    409 with the run's status when the run is not running."""
    run = _run_or_404(run_id)
    principal = _require_can_steer(request, run)

    mode = (body.mode or steering.MODE_INJECT).strip().lower()
    if mode not in steering.MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of: {', '.join(steering.MODES)}")
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="The message is empty")
    if len(text) > steering.MAX_BODY_CHARS:
        raise HTTPException(status_code=400,
                            detail=f"The message is longer than {steering.MAX_BODY_CHARS} characters")

    status = str(run.get("status") or "")
    if status != "running":
        raise HTTPException(status_code=409, detail={
            "message": f"The run is not running (status: {status or 'unknown'})",
            "status": status,
        })
    agent_id = str(run.get("agent_id") or "")
    if mode == steering.MODE_INJECT and _agent_is_remote(agent_id):
        raise HTTPException(status_code=409, detail={
            "message": "This agent runs elsewhere and cannot take a message mid-run; interrupt it instead",
            "status": status,
        })

    author_name = getattr(principal, "username", "") or None
    msg = steering.post(run_id, text, mode=mode, author=principal)
    workspace = run.get("workspace")
    result: Dict[str, Any] = {"message": msg, "run_id": run_id}

    if mode == steering.MODE_INJECT:
        result["next"] = "wait"
    else:
        task = _task_of(run)
        is_chat = str(run.get("session_type") or "") == "chat"
        snapshot = _snapshot_continuations(str(task.id)) if task is not None else []
        if not _stop(run_id):
            steering.mark(msg["msg_id"], steering.STATUS_FAILED)
            audit.record("run.steer", principal=principal, object_type="run", object_id=run_id,
                         workspace=workspace, result="error",
                         details={"mode": mode, "msg_id": msg["msg_id"], "error": "stop failed"})
            raise HTTPException(status_code=409, detail={
                "message": "The run could not be stopped", "status": status})
        if is_chat:
            result["next"] = "send"
            result["message"] = steering.mark(msg["msg_id"], steering.STATUS_INTERRUPTED) or msg
        elif task is not None and agent_id:
            try:
                new_run_id = _relaunch_task(run, task, text, author_name)
            except Exception as exc:  # noqa: BLE001 - any launch failure: the stop stands, the message is marked failed and the caller told
                log.warning("steering: relaunch after interrupt failed for run %s", run_id, exc_info=True)
                steering.mark(msg["msg_id"], steering.STATUS_FAILED)
                audit.record("run.steer", principal=principal, object_type="run", object_id=run_id,
                             workspace=workspace, result="error",
                             details={"mode": mode, "msg_id": msg["msg_id"], "error": str(exc)})
                raise HTTPException(status_code=500,
                                    detail=f"The run was stopped but could not be started again: {exc}")
            _restore_continuations(snapshot, str(task.id), new_run_id)
            try:
                from tasks import service as tasks_service
                tasks_service.append_task_activity_log(
                    task.id, "steer_interrupt",
                    f"Run interrupted by {author_name or 'the user'} with a message; "
                    f"the agent continues in a new run: {text[:500]}",
                    run_id=run_id, next_run_id=new_run_id, agent_id=agent_id,
                    msg_id=msg["msg_id"],
                )
            except Exception:  # noqa: BLE001 - an activity-log write is best-effort
                log.debug("steering: activity log failed for %s", task.id, exc_info=True)
            result["next"] = "relaunched"
            result["next_run_id"] = new_run_id
            result["message"] = steering.mark(
                msg["msg_id"], steering.STATUS_INTERRUPTED, next_run_id=new_run_id) or msg
        else:
            # Neither a chat turn nor a task run (a flow node, a delegation):
            # stopped, and nothing here knows how to carry the message on.
            result["next"] = "none"
            result["message"] = steering.mark(msg["msg_id"], steering.STATUS_INTERRUPTED) or msg

    audit.record("run.steer", principal=principal, object_type="run", object_id=run_id,
                 workspace=workspace,
                 details={"mode": mode, "msg_id": msg["msg_id"], "next": result.get("next"),
                          "next_run_id": result.get("next_run_id")})
    return result


@router.get("/api/runs/{run_id}/steer")
async def list_run_steering(run_id: str, request: Request):
    """The messages sent to a run, oldest first, each with its state."""
    run = _run_or_404(run_id)
    access.require_visible(identity.request_principal(request), run.get("workspace"))
    status = str(run.get("status") or "")
    if status not in _LIVE_STATUSES and str(run.get("session_type") or "") != "chat":
        # The run is over: whatever it never took will not be taken now. A
        # chat turn settles its own (chat/pipelines.py hands them back to the
        # client as the next turn), so a read here must not get there first.
        steering.mark_expired(run_id)
    return {"run_id": run_id, "status": status, "messages": steering.list_for_run(run_id)}
