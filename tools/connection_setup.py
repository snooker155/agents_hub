"""
The agent's side of connection proposals (connectors/proposals.py,
docs/connectors.md "Setting up from the chat").

Three tools:

- ``connection_options`` says what can be connected and what is already
  there: the connectors and channels with their fields, the MCP servers,
  database connections, watchers and secret names of this workspace. Names
  and flags only, never a value.
- ``propose_connection`` prepares one connection with the non-secret fields
  filled in. In the dashboard chat the turn waits on a card where the person
  edits the fields, types the secrets and presses Connect (or Deny); the tool
  then answers with what happened, test result included. Anywhere else
  (Telegram, a task, a schedule) the proposal waits on the Connectors page and
  the tool answers at once with its id.
- ``connection_proposal_status`` reads such a proposal back later.

The agent never holds a secret here: a value in a secret field is refused,
and the person's secrets go from the card straight to the hub. Nothing is
changed by these tools themselves; the change is made by
``POST /api/connection-proposals/{id}/apply`` as the person who answered.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

log = logging.getLogger(__name__)

#: How long a proposal made outside a chat (nobody in front of a card) stays
#: open on the Connectors page.
OFFLINE_TIMEOUT_SECONDS = 3 * 24 * 3600.0


def _run() -> Dict[str, str]:
    """``run_id``, ``agent_id`` and ``workspace`` of the run calling the tool."""
    out = {"run_id": "", "agent_id": "", "workspace": ""}
    try:
        from agents.agent_loop import current_state
        state = current_state()
    except Exception:  # noqa: BLE001 - outside the loop: the environment and context vars below
        state = None
    if state is not None:
        out.update(run_id=str(state.run_id or ""), agent_id=str(state.agent_id or ""),
                   workspace=str(state.workspace or ""))
    out["run_id"] = out["run_id"] or os.environ.get("AGENT_RUN_ID", "")
    if not out["agent_id"]:
        try:
            from common.agent_context import current_agent_id
            out["agent_id"] = current_agent_id.get() or os.environ.get("AGENT_ID", "") or ""
        except Exception:  # noqa: BLE001 - the agent id is a label on the card
            out["agent_id"] = os.environ.get("AGENT_ID", "")
    if not out["workspace"]:
        try:
            from common.workspace_context import resolve_active_workspace
            out["workspace"] = resolve_active_workspace() or ""
        except Exception:  # noqa: BLE001 - a workspace kind then reports that there is none
            out["workspace"] = ""
    return out


def _acting_user() -> Optional[str]:
    """Who the run acts for, so they (besides an admin) may finish the proposal."""
    try:
        from common import identity
        return identity.current_user_id() or None
    except Exception:  # noqa: BLE001 - nobody known: an admin finishes it
        return None


def _fingerprint(proposal: Dict[str, Any]) -> str:
    raw = json.dumps({k: proposal.get(k) for k in ("kind", "target", "workspace", "fields")},
                     sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _notify_offline(proposal: Dict[str, Any], approval_id: str, reason: str, agent_id: str) -> None:
    try:
        from plans.service import create_notification
        create_notification(
            title=f"An agent proposes a connection: {proposal.get('title') or proposal.get('target')}",
            body=(f"{agent_id or 'An agent'} prepared a {proposal.get('kind')} connection. "
                  f"Finish it or deny it on the Connectors page. {reason}").strip(),
            severity="info", source={"connection_proposal": approval_id},
            workspace=proposal.get("workspace"))
    except Exception:  # noqa: BLE001 - the proposal waits on the Connectors page either way
        log.debug("connection proposal: inbox notification failed", exc_info=True)


def _answer(row: Dict[str, Any], proposal: Dict[str, Any], timeout: float) -> str:
    from common import tool_approvals as ta

    status = str(row.get("status") or "")
    note = str(row.get("note") or "").strip()
    who = str(row.get("decided_by_name") or "").strip()
    base = {"proposal_id": row.get("approval_id"), "kind": proposal.get("kind"),
            "target": proposal.get("target"), "status": status}
    if status == ta.STATUS_APPROVED:
        return json_ok({**base, "status": "applied", "applied_by": who or None,
                        "outcome": note or "Applied."})
    if status == ta.STATUS_DENIED:
        return json_err("The person denied this connection." + (f" Their note: {note}" if note else ""),
                        code="denied", extra=base)
    if status == ta.STATUS_CANCELLED:
        return json_err("The run was stopped while the proposal waited.", code="cancelled", extra=base)
    return json_err(f"Nobody answered within {int(timeout)} seconds; the proposal expired. "
                    "Ask the person whether to propose it again.", code="expired", extra=base)


# ── connection_options ───────────────────────────────────────────────────────

class OptionsInput(BaseModel):
    kind: Optional[str] = Field(
        default=None,
        description="One of connector, channel, mcp_server, database, watcher, secret; empty for all")


@tool("connection_options", args_schema=OptionsInput)
def connection_options(kind: Optional[str] = None) -> str:
    """List what can be connected and what already is: connectors and chat channels with
    their fields (which are secret, which required), and this workspace's MCP servers,
    database connections, watchers and secret names. Never returns a value.

    Call it before propose_connection to learn the exact field keys.
    """
    from connectors import proposals
    ctx = _run()
    try:
        return json_ok({"workspace": ctx["workspace"] or None,
                        "options": proposals.options(kind or None, workspace=ctx["workspace"] or None)},
                       default=str)
    except proposals.ProposalError as exc:
        return json_err(str(exc), code=exc.code)


# ── propose_connection ───────────────────────────────────────────────────────

class ProposeInput(BaseModel):
    kind: str = Field(..., description="connector (jira, linear, google, microsoft, notion, confluence), "
                                       "channel (slack, discord, teams, mail), mcp_server, database, "
                                       "watcher or secret")
    target: str = Field(default="", description="The connector or channel name; for mcp_server the "
                                                "server id; for database and watcher the name; for "
                                                "secret the secret name (UPPER_SNAKE_CASE)")
    # Not "config": LangChain hands a parameter of that name its own run config.
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret fields only, by the keys connection_options lists (e.g. "
                    "{\"base_url\": \"https://acme.atlassian.net\", \"email\": \"a@b.c\"}). Leave every "
                    "secret out: the person types it into the card. For an MCP server, name a secret "
                    "header or env variable with an empty value: {\"headers\": {\"Authorization\": \"\"}}")
    reason: str = Field(default="", max_length=500,
                        description="One sentence the person sees: why this connection")


@tool("propose_connection", args_schema=ProposeInput)
def propose_connection(kind: str, target: str = "", fields: Optional[Dict[str, Any]] = None,
                       reason: str = "") -> str:
    """Prepare a connection for the person to finish: a connector, chat channel, MCP server,
    read-only database connection, watcher or workspace secret.

    You fill the non-secret fields; the person sees a card in the chat, can edit them, types
    the secrets (you never see them) and presses Connect or Deny. In the chat this call waits
    for that answer and returns what happened, including the connection test. Outside the
    chat it returns at once with status pending and a proposal_id; the person finishes it on
    the Connectors page. Never ask the person to paste a token into the conversation: propose
    the connection instead.
    """
    from common import tool_approvals as ta
    from connectors import proposals

    ctx = _run()
    try:
        proposal = proposals.build(kind, target, fields or {}, workspace=ctx["workspace"] or None)
    except proposals.ProposalError as exc:
        return json_err(str(exc), code=exc.code)
    if not ctx["run_id"]:
        return json_err("A proposal needs a run to belong to, and this call has none.", code="no_run")
    reason = str(reason or "").strip()[:500]
    fingerprint = _fingerprint(proposal)
    chat = ta.chat_context(ctx["run_id"])
    if chat is not None:
        timeout = ta.timeout_seconds()
        try:
            row = ta.hold(chat, tool=proposals.TOOL, tool_input=proposal, reason=reason,
                          fingerprint=fingerprint, by="setup", agent_id=ctx["agent_id"],
                          workspace=proposal.get("workspace") or ctx["workspace"] or None,
                          timeout_s=timeout)
        except Exception:  # noqa: BLE001 - a broken wait is reported, never a half applied change
            log.warning("connection proposal: could not hold in chat", exc_info=True)
            row = None
        if row is not None:
            return _answer(row, proposal, timeout)
    # Nobody in front of a card: the proposal waits on the Connectors page.
    try:
        row = ta.transport_for(ctx["run_id"]).open_tool_approval({
            "run_id": ctx["run_id"], "tool": proposals.TOOL, "tool_input": proposal,
            "reason": reason, "fingerprint": fingerprint, "by": "setup", "hook": "",
            "agent_id": ctx["agent_id"], "workspace": proposal.get("workspace") or ctx["workspace"] or None,
            "conversation_id": None, "owner": _acting_user(), "timeout_s": OFFLINE_TIMEOUT_SECONDS,
        })
    except Exception as exc:  # noqa: BLE001 - reported to the agent
        log.warning("connection proposal: could not record", exc_info=True)
        return json_err(f"Could not record the proposal: {type(exc).__name__}", code="error")
    approval_id = str((row or {}).get("approval_id") or "")
    if not approval_id:
        return json_err("Could not record the proposal.", code="error")
    _notify_offline(proposal, approval_id, reason, ctx["agent_id"])
    return json_ok({"proposal_id": approval_id, "status": "pending", "kind": proposal["kind"],
                    "target": proposal["target"], "where": "/connectors",
                    "message": "The person finishes or denies it on the Connectors page; check later "
                               "with connection_proposal_status."})


# ── connection_proposal_status ───────────────────────────────────────────────

class StatusInput(BaseModel):
    proposal_id: str = Field(..., description="The proposal_id propose_connection returned")


@tool("connection_proposal_status", args_schema=StatusInput)
def connection_proposal_status(proposal_id: str) -> str:
    """Read back a connection proposal: pending, applied (with the outcome), denied or expired."""
    from common import tool_approvals as ta
    from connectors import proposals

    ctx = _run()
    try:
        row = ta.transport_for(ctx["run_id"]).tool_approval(str(proposal_id or "").strip())
    except Exception:  # noqa: BLE001 - an unreadable row is reported as not found
        row = None
    if not row or row.get("tool") != proposals.TOOL:
        return json_err(f"No connection proposal '{proposal_id}'.", code="not_found")
    proposal = row.get("input") if isinstance(row.get("input"), dict) else {}
    if row.get("status") == ta.STATUS_PENDING and ta.is_expired(row):
        return json_err("Nobody finished this proposal in time; it expired. Ask the person whether "
                        "to propose it again.", code="expired",
                        extra={"proposal_id": row.get("approval_id"), "status": ta.STATUS_EXPIRED})
    if row.get("status") == ta.STATUS_PENDING:
        return json_ok({"proposal_id": row.get("approval_id"), "status": "pending",
                        "kind": proposal.get("kind"), "target": proposal.get("target"),
                        "expires_at": row.get("expires_at")})
    return _answer(row, proposal, OFFLINE_TIMEOUT_SECONDS)


CONNECTOR_TOOLS = [connection_options, propose_connection, connection_proposal_status]

__all__ = ["CONNECTOR_TOOLS", "connection_options", "connection_proposal_status", "propose_connection"]
