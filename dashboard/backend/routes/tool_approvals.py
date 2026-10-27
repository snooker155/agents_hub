"""
Routes: answering a tool call that waits in a chat turn (common/tool_approvals.py,
docs/hooks.md, "In chat").

For a person:

- ``GET /api/runs/{run_id}/tool-approvals`` lists the calls a run waited on
  (``?status=pending`` for the ones still waiting), so a chat reopened while
  its turn waits can show the card again.
- ``GET /api/flow-runs/{flow_run_id}/tool-approvals`` lists the calls the
  nodes of a background flow run waited on (``?status=pending`` likewise):
  the flow run's page, the loop's page and the chat that started the run
  show their cards from it ("In a background flow, loop or team").
- ``GET /api/team-runs/{team_run_id}/tool-approvals`` does the same for the
  members of a team run: the team page and the chat that started it.
- ``GET /api/tool-approvals/{approval_id}`` reads one.
- ``POST /api/tool-approvals/{approval_id}`` answers one: ``{"decision":
  "approve"|"deny", "note": "..."}``. 409 when the call is no longer waiting
  (answered by someone else, timed out, or the run was stopped).

Who may answer: the one operator, from the dashboard. No agent tool reaches
this route, so an agent never approves its own call. The waiting run notes
the answer on its policy trail as ``human_approved`` or ``human_denied``.

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

from common import identity, tool_approvals

log = logging.getLogger(__name__)

router = APIRouter(tags=["tool-approvals"])


class DecideBody(BaseModel):
    decision: str
    note: str = ""


def _load(approval_id: str) -> Dict[str, Any]:
    approval = tool_approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="no such approval request")
    return approval


@router.get("/api/runs/{run_id}/tool-approvals")
def list_run_approvals(run_id: str, status: Optional[str] = None):
    from managers.run_manager import get_run_by_id
    run = get_run_by_id(run_id) or {}
    rows = tool_approvals.list_for_run(run_id, status=status or None)
    if not run and not rows:
        raise HTTPException(status_code=404, detail="no such run")
    return {"approvals": rows}


@router.get("/api/flow-runs/{flow_run_id}/tool-approvals")
def list_flow_run_approvals(flow_run_id: str, status: Optional[str] = None):
    return {"approvals": tool_approvals.list_for_flow_run(flow_run_id, status=status or None)}


@router.get("/api/team-runs/{team_run_id}/tool-approvals")
def list_team_run_approvals(team_run_id: str, status: Optional[str] = None):
    return {"approvals": tool_approvals.list_for_team_run(team_run_id, status=status or None)}


@router.get("/api/tool-approvals/{approval_id}")
def get_approval(approval_id: str):
    return {"approval": _load(approval_id)}


@router.post("/api/tool-approvals/{approval_id}")
def decide_approval(approval_id: str, body: DecideBody, request: Request):
    return answer(_load(approval_id), identity.request_principal(request), body.decision, body.note)


def answer(approval: Dict[str, Any], principal: Any, decision: str,
           note: str = "") -> Dict[str, Any]:
    """Settle a waiting call as the operator. Raises 400 for an unknown
    decision, 409 when the call no longer waits."""
    approval_id = str(approval.get("approval_id") or "")
    decision = str(decision or "").strip().lower()
    if decision not in tool_approvals.DECISIONS:
        raise HTTPException(status_code=400, detail="decision must be approve or deny")
    author = principal or "operator"
    settled = tool_approvals.decide(approval_id, decision, note=note, author=author)
    if settled is None:
        current = tool_approvals.get(approval_id) or approval
        raise HTTPException(status_code=409, detail={
            "message": "This call is no longer waiting for an answer.",
            "status": current.get("status")})
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
    flow_run_id: Optional[str] = None
    team_run_id: Optional[str] = None
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
