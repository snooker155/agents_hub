"""
The agent's two tools: ``request_account_access`` and ``revoke_account_access``.

Built per agent, like the handoff tool: agents/agent_factory.py adds them only
to an agent whose consent settings name at least one provider, and a call
outside a widget or channel turn (no end user bound, common/secrets.py)
answers that it needs one. Neither touches anything but the end user's own
grant: the first hands out a link the end user opens themselves, the second
takes back what they gave, so both sit outside the approval guard the way the
handoff tool does.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

from . import access, catalog, store

log = logging.getLogger(__name__)

REQUEST_TOOL = "request_account_access"
REVOKE_TOOL = "revoke_account_access"
TOOL_NAMES = (REQUEST_TOOL, REVOKE_TOOL)


class RequestAccessInput(BaseModel):
    provider: str = Field(..., description="'google' or 'microsoft'")
    purpose: str = Field("", max_length=store.PURPOSE_MAX,
                         description="One sentence the person sees on the consent page: what you "
                                     "will do with the access, e.g. 'to book the meeting in your calendar'")


class RevokeAccessInput(BaseModel):
    provider: str = Field(..., description="'google' or 'microsoft'")


def _current_run_id() -> Optional[str]:
    try:
        from common.agent_context import current_session_id
        return current_session_id.get() or None
    except Exception:  # noqa: BLE001 - the run id is a courtesy on the row
        return None


def request_account_access(provider: str, purpose: str = "") -> str:
    from .flow import ConsentFlowError, request_access
    try:
        out = request_access(provider, purpose, run_id=_current_run_id())
    except ConsentFlowError as exc:
        return json_err(str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
        log.warning("request_account_access failed", exc_info=True)
        return json_err(f"Could not create the access link: {type(exc).__name__}", code="error")
    if out.get("already_granted"):
        return json_ok({**out, "message": "The person already gave this access; go ahead with "
                                          "the task."})
    return json_ok({**out, "message": "Send the person this link. It opens a page that says what "
                                      "you asked for; after they agree, ask them to come back "
                                      "here and say so. The link works once and expires."})


def revoke_account_access(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    if not catalog.is_provider(provider):
        return json_err(f"provider must be one of: {', '.join(catalog.PROVIDERS)}", code="bad_provider")
    scope = access.turn_scope()
    if scope is None:
        return json_err("This works only in a widget or chat channel conversation.", code="no_end_user")
    workspace, agent_id, principal = scope
    row = store.grant_row(workspace, agent_id, principal, provider) or {}
    try:
        done = access.revoke(workspace, agent_id, principal, provider, by="end_user",
                             actor=access.end_user_actor(principal, row.get("account_email") or ""))
    except Exception as exc:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
        log.warning("revoke_account_access failed", exc_info=True)
        return json_err(f"Could not remove the access: {type(exc).__name__}", code="error")
    if not done:
        return json_ok({"revoked": False, "provider": provider,
                        "message": "There was no access to remove."})
    return json_ok({"revoked": True, "provider": provider,
                    "message": f"The {catalog.PROVIDER_LABELS[provider]} access is removed."})


PROMPT = """## Access to the person's own accounts
In a widget or chat channel conversation you act on the person's own {labels} account, never on the hub's. When a task needs it and a tool answers that access is missing, call `request_account_access` with the provider and one sentence of purpose, send the person the link it returns, and continue once they say they agreed. When they ask to disconnect, call `revoke_account_access`. Never ask them for a password or a code."""


def consent_tools_for(spec: Any) -> Tuple[List[StructuredTool], str]:
    """``(tools, prompt)`` for an agent whose consent settings name a
    provider, else ``([], "")``."""
    agent_id = str(getattr(spec, "id", "") or "")
    if not agent_id:
        return [], ""
    providers = access.agent_providers(agent_id)
    if not providers:
        return [], ""
    labels = " or ".join(catalog.PROVIDER_LABELS[p] for p in providers)
    tools = [
        StructuredTool.from_function(
            name=REQUEST_TOOL, func=request_account_access, args_schema=RequestAccessInput,
            description=("Ask the person in this widget or channel conversation for access to "
                         f"their own {labels} account. Returns a link to send them; the page "
                         "says in plain words what access is asked and why.")),
        StructuredTool.from_function(
            name=REVOKE_TOOL, func=revoke_account_access, args_schema=RevokeAccessInput,
            description="Remove the access the person gave to their own account, when they ask "
                        "to disconnect."),
    ]
    return tools, PROMPT.format(labels=labels)


__all__ = ["PROMPT", "REQUEST_TOOL", "REVOKE_TOOL", "TOOL_NAMES", "consent_tools_for",
           "request_account_access", "revoke_account_access"]
