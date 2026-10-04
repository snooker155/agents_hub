"""
``hub_lookup``: one read-only tool over the hub's records, as the person behind
the turn sees them (chat/lookup.py, docs/assistant.md "What it can look up").

The assistant's way to answer a question about any page without a tool per
page: a kind, an optional search text, an optional id. Without an id it lists,
with one it describes; every result carries ``url``, the page that shows it,
for a "show on screen" link. It never returns a run's answer, a log or a
chat's messages (metadata only), so it reads private data but no untrusted
text (tools/capabilities.py).
"""
from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

#: The one agent whose lookups reach every workspace the person can see.
ASSISTANT_AGENT_ID = "assistant"


def _calling_agent() -> str:
    """The agent whose turn calls the tool: the context an invocation sets
    (agents/agent_invoke.py), else the running loop's own record, which a chat
    turn has even when it calls ``arun`` directly (chat/entity_chat.py)."""
    from common.agent_context import current_agent_id
    agent_id = current_agent_id.get()
    if agent_id:
        return str(agent_id)
    try:
        from agents.agent_loop import current_state
        state = current_state()
    except Exception:  # noqa: BLE001 - no loop module: nobody to name
        state = None
    return str(getattr(state, "agent_id", "") or "")


class HubLookupInput(BaseModel):
    kind: str = Field(..., description=(
        "What to look up: run (alias message), session, cost, budget, model, agent, notification, "
        "approval, task, view, project, scenario, loop, flow, team, job"))
    query: Optional[str] = Field(None, description="Text to filter the list by (a name, a status, an agent id)")
    id: Optional[str] = Field(None, description=(
        "Describe one record instead of listing: a run id, a session id, an agent id, 'provider/model', "
        "a workspace name for budget, or today, week or month for cost"))
    workspace: Optional[str] = Field(None, description=(
        "A workspace the person can reach, or 'all'; empty for the one this turn runs in"))
    limit: int = Field(10, ge=1, le=30, description="Most rows to list")


@tool("hub_lookup", args_schema=HubLookupInput)
def hub_lookup(kind: str, query: Optional[str] = None, id: Optional[str] = None,
               workspace: Optional[str] = None, limit: int = 10) -> str:
    """Look up the hub's records as the person you act for sees them: runs and
    their cost, sessions, spend this month and their limit, budgets, models,
    agents and their tools, unread notifications, approvals waiting, tasks,
    flows, teams and the rest. Without `id` it lists; with `id` it describes one
    record. Each result has a `url`: link it as "show on screen". For "what is
    new" look up `notification` and `approval`; for "how much did I spend" look
    up `cost` with id `month`.
    """
    from chat import lookup
    from common.attribution import launching_user
    from common.workspace_context import resolve_active_workspace
    try:
        return json_ok(lookup.lookup(
            kind, query=query or "", entity_id=id or "", workspace=workspace or "",
            limit=limit, user_id=launching_user(), current=resolve_active_workspace(),
            # Only the assistant reaches past its turn's workspace: its reach is
            # the person's, any other agent's is its own workspace.
            cross_workspace=_calling_agent() == ASSISTANT_AGENT_ID))
    except lookup.LookupError_ as exc:
        return json_err(str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        return json_err(f"The lookup failed: {exc}", code="internal")


HUB_LOOKUP_TOOLS = [hub_lookup]

__all__ = ["hub_lookup", "HUB_LOOKUP_TOOLS"]
