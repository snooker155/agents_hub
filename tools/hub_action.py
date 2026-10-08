"""
``hub_action``: one-step changes to the hub's records from a conversation
(chat/actions.py, docs/assistant.md "What it can change").

Stop or restart an instance, pause or resume a service, a watcher or a
proactive agent, cancel an eval run and the like: a kind, a verb and an id.
Every call waits for the person's yes on a card (tools/approval.py
``ALWAYS_GATED``), and runs with the person's role in the record's workspace.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok
from tools.hub_lookup import ASSISTANT_AGENT_ID, _calling_agent


class HubActionInput(BaseModel):
    kind: str = Field(..., description="The kind of record, as hub_lookup names it: instance, service, watcher, ...")
    action: str = Field(..., description="stop, start, restart, pause, resume, enable, disable or cancel")
    id: str = Field(..., description="The record's id, from hub_lookup")
    workspace: Optional[str] = Field(None, description="The record's workspace when the id alone is ambiguous")


def _where() -> dict:
    from common.attribution import launching_user
    from common.workspace_context import resolve_active_workspace
    return {"user_id": launching_user(), "current": resolve_active_workspace(),
            "cross_workspace": _calling_agent() == ASSISTANT_AGENT_ID}


@tool("hub_action", args_schema=HubActionInput)
def hub_action(kind: str, action: str, id: str, workspace: Optional[str] = None) -> str:
    """Do one small thing to a record the person can reach: stop or restart an
    instance; pause or resume a service, a watcher or a pulse (proactive
    agent); stop a project deployment or a browser session; cancel an eval run;
    enable or disable a guardrail, a connection, an MCP server or a widget.
    Look the record up with hub_lookup first and say what will happen; the
    person then answers a card (or says yes), and only then the action runs.
    Nothing here creates, edits or deletes.
    """
    from chat import actions
    from chat.lookup import LookupError_
    try:
        return json_ok(actions.perform(kind, action, id, workspace=workspace or "", **_where()))
    except LookupError_ as exc:
        return json_err(str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        return json_err(f"The action failed: {exc}", code="internal")


def describe_call(tool_input: Any) -> str:
    """The approval card's sentence for one ``hub_action`` call, or "" when
    the call names nothing that exists (the call itself then says why)."""
    from chat import actions
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return ""
    if not isinstance(tool_input, dict):
        return ""
    try:
        return actions.describe(str(tool_input.get("kind") or ""), str(tool_input.get("action") or ""),
                                str(tool_input.get("id") or ""),
                                workspace=str(tool_input.get("workspace") or ""), **_where())
    except Exception:  # noqa: BLE001 - the card falls back to the generic sentence
        return ""


HUB_ACTION_TOOLS = [hub_action]

__all__ = ["HUB_ACTION_TOOLS", "describe_call", "hub_action"]
