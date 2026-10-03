"""
Routes: answering a tool call that waits in a chat turn (common/tool_approvals.py,
docs/hooks.md, "In chat").

For a person:

- ``GET /api/runs/{run_id}/tool-approvals`` lists the calls a run waited on
  (``?status=pending`` for the ones still waiting), so a chat reopened while
  its turn waits can show the card again.
- ``GET /api/tool-approvals/{approval_id}`` reads one.
- ``POST /api/tool-approvals/{approval_id}`` answers one: ``{"decision":
  "approve"|"deny", "note": "..."}``. 409 when the call is no longer waiting
  (answered by someone else, timed out, or the run was stopped).

Who may answer: someone who sees the run's workspace and is the run's owner or
an admin; outside ``multi`` mode, the one operator. The rule steering's
``system`` mode follows (routes/steering.py), and for the same reason the
service credential is always refused: it is what a run's own process presents
to this API, and an agent must never approve its own call. Every answer lands
in the audit log as ``tool.approval``; the waiting run adds its own
``tool.policy`` row with ``human_approved`` or ``human_denied``.

For the waiting run (under ``/api/run-state``, like the steering claim the
loop makes; ``common.state_transport.HttpStateTransport`` calls these from a
run container whose state mount is read-only):

- ``POST /api/run-state/runs/{run_id}/tool-approvals`` records a waiting call;
- ``GET /api/run-state/tool-approvals/{approval_id}`` reads it back;
- ``POST /api/run-state/tool-approvals/{approval_id}/close`` ends it on a
  timeout or a stop.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity, tool_approvals

log = logging.getLogger(__name__)

router = APIRouter(tags=["tool-approvals"])


class DecideBody(BaseModel):
    decision: str
    note: str = ""


def _run_of(approval: Dict[str, Any]) -> Dict[str, Any]:
    from managers.run_manager import get_run_by_id
    return get_run_by_id(str(approval.get("run_id") or "")) or {}


def _workspace(approval: Dict[str, Any], run: Dict[str, Any]) -> Optional[str]:
    return run.get("workspace") or approval.get("workspace")


def _owner(approval: Dict[str, Any], run: Dict[str, Any]) -> Optional[str]:
    """The user who may answer besides an admin: who the call recorded, else
    the conversation's owner (a call written before the run knew its owner)."""
    owner = str(approval.get("owner") or "")
    if owner:
        return owner
    conv_id = str(run.get("task_id") or run.get("conversation_id") or "")
    if conv_id and str(run.get("session_type") or "") == "chat":
        try:
            from common import chat_store
            return str((chat_store.get_chat(conv_id) or {}).get("owner") or "") or None
        except Exception:  # noqa: BLE001 - an unknown owner leaves the call to admins
            log.debug("tool approvals: owner lookup failed for %s", conv_id, exc_info=True)
    return None


def _require_answerer(request: Request, approval: Dict[str, Any], run: Dict[str, Any]):
    """The caller's principal, once they may answer this call; 403 otherwise."""
    from common.auth import MULTI

    principal = identity.request_principal(request)
    access.require_visible(principal, _workspace(approval, run))
    if principal is not None and getattr(principal, "kind", "") == "service":
        raise HTTPException(status_code=403,
                            detail="a tool call is approved by a person, not by an agent or a run")
    if principal is None:
        if identity.current_mode() == MULTI:
            raise HTTPException(status_code=403, detail="sign in to answer this call")
        return None
    if principal.is_admin or identity.current_mode() != MULTI:
        return principal
    owner = _owner(approval, run)
    # owner_or_admin reads a "local" owner (filed before identity existed) as
    # anyone's, the rule every other owned record follows.
    if owner and access.owner_or_admin(principal, owner):
        return principal
    raise HTTPException(status_code=403, detail="only the run's owner or an admin may answer this call")


def _require_visible(request: Request, approval: Dict[str, Any], run: Dict[str, Any]) -> None:
    access.require_visible(identity.request_principal(request), _workspace(approval, run))


def _load(approval_id: str) -> Dict[str, Any]:
    approval = tool_approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="no such approval request")
    return approval


@router.get("/api/runs/{run_id}/tool-approvals")
def list_run_approvals(run_id: str, request: Request, status: Optional[str] = None):
    from managers.run_manager import get_run_by_id
    run = get_run_by_id(run_id) or {}
    rows = tool_approvals.list_for_run(run_id, status=status or None)
    if not run and not rows:
        raise HTTPException(status_code=404, detail="no such run")
    access.require_visible(identity.request_principal(request),
                           run.get("workspace") or (rows[0].get("workspace") if rows else None))
    return {"approvals": rows}


@router.get("/api/tool-approvals/{approval_id}")
def get_approval(approval_id: str, request: Request):
    approval = _load(approval_id)
    _require_visible(request, approval, _run_of(approval))
    return {"approval": approval}


@router.post("/api/tool-approvals/{approval_id}")
def decide_approval(approval_id: str, body: DecideBody, request: Request):
    approval = _load(approval_id)
    run = _run_of(approval)
    principal = _require_answerer(request, approval, run)
    decision = str(body.decision or "").strip().lower()
    if decision not in tool_approvals.DECISIONS:
        raise HTTPException(status_code=400, detail="decision must be approve or deny")
    author = principal or "operator"
    settled = tool_approvals.decide(approval_id, decision, note=body.note, author=author)
    details = {"run_id": approval.get("run_id"), "tool": approval.get("tool"),
               "decision": decision, "note": (body.note or "").strip()[:500],
               "agent_id": approval.get("agent_id"), "approval_id": approval_id}
    if settled is None:
        current = tool_approvals.get(approval_id) or approval
        audit.record("tool.approval", principal=principal, object_type="tool",
                     object_id=approval.get("tool"), workspace=_workspace(approval, run),
                     result="error", details={**details, "status": current.get("status")})
        raise HTTPException(status_code=409, detail={
            "message": "This call is no longer waiting for an answer.",
            "status": current.get("status")})
    audit.record("tool.approval", principal=principal, object_type="tool",
                 object_id=approval.get("tool"), workspace=_workspace(approval, run),
                 result=settled.get("status") or "ok", details=details)
    return {"approval": settled}


# ── the waiting run's side (common/state_transport.py) ───────────────────────

class OpenBody(BaseModel):
    tool: str
    tool_input: Any = None
    reason: str = ""
    fingerprint: str = ""
    by: str = ""
    hook: str = ""
    agent_id: str = ""
    workspace: Optional[str] = None
    conversation_id: Optional[str] = None
    owner: Optional[str] = None
    timeout_s: float = tool_approvals.DEFAULT_TIMEOUT_SECONDS


class CloseBody(BaseModel):
    status: str
    note: str = ""


@router.post("/api/run-state/runs/{run_id}/tool-approvals")
def open_for_run(run_id: str, body: OpenBody):
    """Mirrors ``common.tool_approvals.open_approval``."""
    return {"approval": tool_approvals.open_approval(run_id=run_id, **body.model_dump())}


@router.get("/api/run-state/tool-approvals/{approval_id}")
def read_for_run(approval_id: str):
    """Mirrors ``common.tool_approvals.get``."""
    return {"approval": _load(approval_id)}


@router.post("/api/run-state/tool-approvals/{approval_id}/close")
def close_for_run(approval_id: str, body: CloseBody):
    """Mirrors ``common.tool_approvals.close``: a timeout or a stop."""
    _load(approval_id)
    try:
        return {"approval": tool_approvals.close(approval_id, body.status, note=body.note)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
