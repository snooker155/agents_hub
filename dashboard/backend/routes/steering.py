"""
Routes: steering a running run (docs/steering.md).

``POST /api/runs/{run_id}/steer`` takes a message for a run that is working
right now, in one of three modes:

- ``inject``: stored (``common.steering``) and taken by the agent loop before
  its next model call (``agents/loop_ext/steering.py``). The run keeps going.
- ``interrupt``: stored, then the run is stopped through the same path the
  Stop buttons use (``managers.run_manager.stop_run_by_id``). A task run is
  then launched again on the same task and agent, with the message in its
  instruction; a chat turn is answered with ``{"next": "send"}`` and the
  client sends the message as its next turn itself, since it owns the
  conversation's history. With ``send`` (the run page, which does not own the
  conversation) this route starts that turn itself: ``{"next": "sent"}``.
- ``system``: stored and taken by the loop like an inject, but appended to
  the run's system prompt for the rest of the run. Only an operator may send
  it: the run's owner (the chat's owner, the person who filed the task) or an
  admin, never the service credential an agent's own process carries, so no
  agent and no delegated run can raise its own instructions.
- ``switch_model``: the message is a catalog model id (``provider/model``,
  checked against the enabled models and stored in that form). The loop
  moves the run to that model before its next model call and keeps going
  with its whole trail. Anyone who may steer the run may switch it, except
  the service credential: an agent must not move its own run to another
  (and possibly dearer) model.

``run_id`` may also name a team run (teams/store.py): an inject is posted on
the team's board as the user's message for the next member to read, an
interrupt stops the team.

``GET /api/runs/{run_id}/steer`` lists what was sent to a run and where each
message is (pending, delivered at step N, expired, interrupted). A message
the run never took before it finished is marked expired on the first read
after the run ended.

Access follows the other single-run routes: the caller must see the run's
workspace, and, being a write, steering in ``multi`` mode also needs an
editor's role there; a ``system`` message also needs the run's owner or an
admin (:func:`_require_operator`). Every write lands in the audit log as
``run.steer``.
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
    # For an interrupted chat turn: send the message as the conversation's
    # next turn from here, rather than leave it to the client. The run page
    # asks for it, since it is not the chat that owns the conversation; the
    # chat itself sends its own next turn.
    send: bool = False


# Background turns started here (an interrupted chat carried on from the run
# page), kept referenced so they are not collected while they run.
_TURNS: "set" = set()


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


def _run_owner(run: Dict[str, Any]) -> Optional[str]:
    """The user a run belongs to: the owner of its conversation for a chat
    turn, the person who filed its task otherwise. None when neither is known
    (a delegated run, a flow node)."""
    try:
        if str(run.get("session_type") or "") == "chat":
            from common import chat_store
            conv_id = str(run.get("conversation_id") or run.get("task_id") or "")
            chat = chat_store.get_chat(conv_id) if conv_id else None
            return str((chat or {}).get("owner") or "") or None
        task = _task_of(run)
        if task is None:
            return None
        return str(getattr(task, "created_by_user", "") or "") or None
    except Exception:  # noqa: BLE001 - an unknown owner leaves the run to admins
        log.debug("steering: owner lookup failed for %s", run.get("run_id"), exc_info=True)
        return None


def _require_operator(principal: Any, run: Dict[str, Any]) -> None:
    """403 unless ``principal`` may add to this run's instructions.

    The service credential is refused first and always: it is what a run's
    own process (and every run it delegates to) presents to this API, so
    accepting it would let an agent rewrite its own system prompt. Then an
    admin may, and outside ``multi`` mode the one operator may. In ``multi``
    mode a member must own the run (:func:`_run_owner`); a run with no known
    owner is left to admins.
    """
    from common.auth import MULTI

    if principal is None or getattr(principal, "kind", "") == "service":
        raise HTTPException(status_code=403,
                            detail="a system message is sent by an operator, not by an agent or a delegated run")
    if principal.is_admin or identity.current_mode() != MULTI:
        return
    owner = _run_owner(run)
    # owner_or_admin reads a "local" owner (filed before identity existed) as
    # anyone's, the rule every other owned record follows.
    if owner and access.owner_or_admin(principal, owner):
        return
    raise HTTPException(status_code=403,
                        detail="only the run's owner or an admin may send a system message")


def _require_not_agent(principal: Any) -> None:
    """403 for the service credential, which every run's own process and
    every delegated run carry: a model switch is a person's decision."""
    if principal is not None and getattr(principal, "kind", "") == "service":
        raise HTTPException(status_code=403,
                            detail="a model switch is sent by a person, not by an agent or a delegated run")


def _catalog_model(text: str) -> str:
    """The canonical ``provider/model`` id of an enabled catalog model, 400
    otherwise (the same lookup delegation and fallback models use)."""
    from tools.delegation import resolve_model
    try:
        provider, model = resolve_model(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return f"{provider}/{model}"


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


def _team_run(run_id: str):
    """The team run with this id, or None. A team run is not a row of
    ``runs``: its members are, and the team itself lives in teams/store.py."""
    try:
        from teams import store as team_store
        return team_store.get_run(run_id)
    except Exception:  # noqa: BLE001 - no teams store is no team run
        return None


def _next_chat_request(run: Dict[str, Any], text: str, *, team_run: Any = None):
    """The ChatRequest that carries an interrupted chat on with ``text``: the
    same conversation, the same target (an agent, a flow or a team)."""
    from chat.models import ChatRequest
    from chat.runs import build_conversation_history

    conv_id = str(run.get("conversation_id") or run.get("task_id") or "")
    if not conv_id:
        return None
    common = {"message": text, "workspace": run.get("workspace"), "conversation_id": conv_id,
              "history": build_conversation_history(conv_id), "source": "steer"}
    if team_run is not None:
        return ChatRequest(team_id=team_run.team_id, **common)
    if run.get("flow_id") and str(run.get("channel") or "") == "chat_flow":
        return ChatRequest(flow_id=str(run["flow_id"]), **common)
    agent_id = str(run.get("agent_id") or "")
    return ChatRequest(agent_id=agent_id, **common) if agent_id else None


def _send_next_turn(request_obj: Any) -> None:
    """Run the next chat turn here, in the background. Every chat pipeline
    broadcasts its turn to the conversation's channel (chat/broadcast.py), so
    anyone with the conversation open sees it arrive."""
    import asyncio

    from chat.pipelines import run_chat_flow_pipeline, run_chat_pipeline, run_chat_team_pipeline

    pipeline = (run_chat_team_pipeline if request_obj.team_id
                else run_chat_flow_pipeline if request_obj.flow_id else run_chat_pipeline)

    async def _pump() -> None:
        try:
            async for _event in pipeline(request_obj):
                pass
        except Exception:  # noqa: BLE001 - the turn records its own failure on its run
            log.warning("steering: the next chat turn failed", exc_info=True)

    task = asyncio.create_task(_pump())
    _TURNS.add(task)
    task.add_done_callback(_TURNS.discard)


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


def _validated(body: SteerBody):
    """``(mode, text)`` of a steer request, 400 on a bad one."""
    mode = (body.mode or steering.MODE_INJECT).strip().lower()
    if mode not in steering.MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of: {', '.join(steering.MODES)}")
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="The message is empty")
    if len(text) > steering.MAX_BODY_CHARS:
        raise HTTPException(status_code=400,
                            detail=f"The message is longer than {steering.MAX_BODY_CHARS} characters")
    if mode == steering.MODE_SWITCH_MODEL:
        text = _catalog_model(text)
    return mode, text


def _steer_team(team_run: Any, body: SteerBody, request: Request) -> Dict[str, Any]:
    """Steer a running team: an inject waits for the next member whose
    prompt is built and is posted on the board as the user's message to
    everyone (teams/runner.py ``_Board.take_steering``); an interrupt stops
    the team, and the chat (or, with ``send``, this route) carries on with the
    message as the conversation's next turn."""
    run_id = team_run.team_run_id
    principal = _require_can_steer(request, {"workspace": team_run.workspace})
    mode, text = _validated(body)
    if mode in steering.OPERATOR_MODES:
        raise HTTPException(status_code=400, detail=(
            "A system message or a model switch goes to one agent run; "
            "steer a team with inject or interrupt"))
    status = str(team_run.status or "")
    if status != "running":
        raise HTTPException(status_code=409, detail={
            "message": f"The team run is not running (status: {status or 'unknown'})",
            "status": status,
        })
    msg = steering.post(run_id, text, mode=mode, author=principal)
    result: Dict[str, Any] = {"message": msg, "run_id": run_id, "team": True}
    if mode == steering.MODE_INJECT:
        result["next"] = "wait"
    else:
        from teams.launcher import stop_team_run
        stop_team_run(run_id)
        result["message"] = steering.mark(msg["msg_id"], steering.STATUS_INTERRUPTED) or msg
        conv_run = {"conversation_id": team_run.conversation_id, "workspace": team_run.workspace}
        next_request = _next_chat_request(conv_run, text, team_run=team_run) if body.send else None
        if next_request is not None:
            _send_next_turn(next_request)
            result["next"] = "sent"
            result["conversation_id"] = next_request.conversation_id
        else:
            result["next"] = "send"
    audit.record("run.steer", principal=principal, object_type="team_run", object_id=run_id,
                 workspace=team_run.workspace,
                 details={"mode": mode, "msg_id": msg["msg_id"], "next": result.get("next")})
    return result


@router.post("/api/runs/{run_id}/steer")
async def steer_run(run_id: str, body: SteerBody, request: Request):
    """Send a message to a running run: ``inject`` it before the next model
    step, or ``interrupt`` the run and carry on with it (see module docstring).
    409 with the run's status when the run is not running. ``run_id`` may also
    name a team run: an inject goes on the team's board for the next member
    to read, an interrupt stops the team."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        team_run = _team_run(run_id)
        if team_run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return _steer_team(team_run, body, request)
    principal = _require_can_steer(request, run)
    if (body.mode or "").strip().lower() == steering.MODE_SWITCH_MODEL:
        _require_not_agent(principal)
    mode, text = _validated(body)
    if mode == steering.MODE_SYSTEM:
        _require_operator(principal, run)

    status = str(run.get("status") or "")
    if status != "running":
        raise HTTPException(status_code=409, detail={
            "message": f"The run is not running (status: {status or 'unknown'})",
            "status": status,
        })
    agent_id = str(run.get("agent_id") or "")
    if mode in steering.LOOP_MODES and _agent_is_remote(agent_id):
        raise HTTPException(status_code=409, detail={
            "message": "This agent runs elsewhere and cannot take a message mid-run; interrupt it instead",
            "status": status,
        })

    author_name = getattr(principal, "username", "") or None
    msg = steering.post(run_id, text, mode=mode, author=principal)
    workspace = run.get("workspace")
    result: Dict[str, Any] = {"message": msg, "run_id": run_id}

    if mode in steering.LOOP_MODES:
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
            result["message"] = steering.mark(msg["msg_id"], steering.STATUS_INTERRUPTED) or msg
            next_request = _next_chat_request(run, text) if body.send else None
            if next_request is not None:
                _send_next_turn(next_request)
                result["next"] = "sent"
                result["conversation_id"] = next_request.conversation_id
            else:
                result["next"] = "send"
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
    """The messages sent to a run (or a team run), oldest first, each with its state."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        team_run = _team_run(run_id)
        if team_run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        access.require_visible(identity.request_principal(request), team_run.workspace)
        return {"run_id": run_id, "status": team_run.status, "team": True,
                "messages": steering.list_for_run(run_id)}
    access.require_visible(identity.request_principal(request), run.get("workspace"))
    status = str(run.get("status") or "")
    if status not in _LIVE_STATUSES and str(run.get("session_type") or "") != "chat":
        # The run is over: whatever it never took will not be taken now. A
        # chat turn settles its own (chat/pipelines.py hands them back to the
        # client as the next turn), so a read here must not get there first.
        steering.mark_expired(run_id)
    return {"run_id": run_id, "status": status, "messages": steering.list_for_run(run_id)}
