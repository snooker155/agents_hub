"""
Routes: finishing a connection an agent proposed (connectors/proposals.py,
docs/connectors.md "Setting up from the chat").

- ``GET  /api/connection-proposals?status=pending&workspace=<ws>`` lists the
  proposals (rows of ``tool_approvals`` whose tool is ``propose_connection``)
  the caller may see. The Connectors page shows the open ones, which is where
  a proposal made outside the chat is finished.
- ``POST /api/connection-proposals/{id}/apply`` with ``{"values": {...},
  "secrets": {...}}`` performs it as the caller and settles the waiting call
  as approved, with the outcome as its note: the agent's turn reads that line.
  400 with the reason when a value is wrong, and nothing changes then; 409
  when the proposal no longer waits.

Deny goes through the ordinary ``POST /api/tool-approvals/{id}``; an approve
there is refused for a proposal, since it would tell the agent the connection
exists when nothing was set up.

Who may apply: whoever may answer the waiting call (the run's owner or an
admin, never the service credential a run presents), and on top of that the
role the page for that thing asks for (``proposals.required_role``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity, tool_approvals
from connectors import proposals

from .tool_approvals import _require_answerer, _run_of, _workspace

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connection-proposals", tags=["connection-proposals"])


class ApplyBody(BaseModel):
    values: Dict[str, Any] = {}
    secrets: Dict[str, str] = {}


def _load(approval_id: str) -> Dict[str, Any]:
    row = tool_approvals.get(approval_id)
    if row is None or row.get("tool") != proposals.TOOL:
        raise HTTPException(status_code=404, detail="no such connection proposal")
    return row


@router.get("")
def list_proposals(request: Request, status: Optional[str] = None, workspace: Optional[str] = None):
    principal = identity.request_principal(request)
    if workspace:
        access.require_visible(principal, workspace)
    rows = tool_approvals.list_for_tool(proposals.TOOL, status=status or None, workspace=workspace or None)
    if status == tool_approvals.STATUS_PENDING:
        rows = [r for r in rows if not tool_approvals.is_expired(r)]
    hub_rows = [r for r in rows if not r.get("workspace")]
    ws_rows = access.filter_by_workspace(principal, [r for r in rows if r.get("workspace")])
    return {"proposals": sorted(hub_rows + ws_rows, key=lambda r: str(r.get("created_at") or ""),
                                reverse=True)}


@router.post("/{approval_id}/apply")
async def apply_proposal(approval_id: str, body: ApplyBody, request: Request):
    approval = _load(approval_id)
    run = _run_of(approval)
    principal = _require_answerer(request, approval, run)
    if approval.get("status") != tool_approvals.STATUS_PENDING or tool_approvals.is_expired(approval):
        raise HTTPException(status_code=409, detail={
            "message": "This proposal is no longer waiting for an answer.",
            "status": approval.get("status")})
    proposal = approval.get("input") if isinstance(approval.get("input"), dict) else {}
    if proposal.get("kind") not in proposals.KINDS:
        raise HTTPException(status_code=400, detail="this proposal is unreadable")
    details = {"approval_id": approval_id, "run_id": approval.get("run_id"),
               "agent_id": approval.get("agent_id"), "kind": proposal.get("kind"),
               "target": proposal.get("target")}
    try:
        prepared = proposals.prepare(proposal, values=body.values, secrets=body.secrets)
    except proposals.ProposalError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    identity.require_role(principal, **proposals.required_role(prepared["proposal"]))
    try:
        outcome = await proposals.perform(prepared, principal=principal)
    except proposals.ProposalError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    audit.record("connection.apply", principal=principal, object_type=proposal.get("kind"),
                 object_id=str(outcome["proposal"].get("target") or ""),
                 workspace=_workspace(approval, run), ip=identity.client_ip(request),
                 result="ok" if outcome.get("ok") else "error",
                 details={**details, "test_ok": outcome.get("ok")})
    settled = tool_approvals.decide(approval_id, "approve", note=outcome["summary"],
                                    author=principal or "operator")
    if settled is None:
        # Applied, but the turn stopped waiting a moment ago (a stop, a timeout).
        settled = tool_approvals.get(approval_id) or approval
        outcome["summary"] += " The agent's turn had already stopped waiting, so it was not told."
    return {"approval": settled,
            "outcome": {k: outcome.get(k) for k in ("ok", "summary", "test", "href")}}
