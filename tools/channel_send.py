"""
channel_send — post a message into a chat on one of the chat channels.

The notify_user shape over Slack, Discord, Microsoft Teams or mail: the
destination is a chat the operator bound on the Connectors page (so the
channel is one the hub already talks on), the payload is agent-written
text, which is why ``tools/capabilities.py`` classifies it CAN_EXFILTRATE.

The message goes out through the bot that serves the run's workspace
(docs/connectors.md "Connectors per workspace"): the workspace's own bot when
it defines one, else the default workspace's.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _scoped(workspace: Optional[str]) -> bool:
    """Whether a named chat must belong to ``workspace``: inside a run of a
    workspace, for every agent but the service's own (common/workspace_scope.py).
    With no workspace at all (the CLI) any bound chat is reachable, as before."""
    if not workspace:
        return False
    from common.workspace_scope import current_agent, is_service_wide
    return not is_service_wide(current_agent())


def _foreign_binding(binding: Optional[Dict[str, Any]], workspace: Optional[str]) -> bool:
    """A chat bound to another workspace. A binding that names no workspace
    is shared, the way notify_workspace treats it."""
    bound_ws = str((binding or {}).get("workspace") or "").strip()
    if not bound_ws:
        return False
    from common.workspace_scope import same_workspace
    return not same_workspace(bound_ws, workspace)


class ChannelSendInput(BaseModel):
    channel: str = Field(..., description="One of: slack, discord, teams, mail, telegram")
    chat_key: Optional[str] = Field(
        None,
        description="The chat to post into (a Slack channel id, a Discord channel id, a Teams "
                    "conversation id, an email address). Omit to post into every chat bound "
                    "to this workspace on that channel.",
    )
    text: str = Field(..., min_length=1, description="The message text")


@tool("channel_send", args_schema=ChannelSendInput)
def channel_send(channel: str, text: str, chat_key: Optional[str] = None) -> str:
    """Post a message into a Slack, Discord, Teams or mail chat the operator has
    bound to this workspace on the Connectors page.

    With no chat_key the message goes to every chat bound to the current
    workspace on that channel, which is how a scheduled or proactive agent
    reports to the team. Returns how many chats were reached.
    """
    channel = (channel or "").strip().lower()
    from common.workspace_context import resolve_active_workspace
    workspace = resolve_active_workspace()

    if channel == "telegram":
        try:
            from connectors.telegram.notify import notify_workspace as tg_notify
            from connectors.telegram.notify import bot_store
            from connectors.telegram.notify import _send_text as tg_send
        except Exception as exc:  # noqa: BLE001
            return _err(f"telegram unavailable: {exc}")
        if chat_key:
            # The bot serving this workspace: its own, else the default's.
            telegram_store = bot_store(workspace)
            token = telegram_store.get_token()
            if not token:
                return _err("telegram is not configured")
            try:
                chat_id = int(str(chat_key).strip())
            except ValueError:
                return _err(f"telegram chat_key must be a numeric chat id, not {chat_key!r}")
            if _scoped(workspace):
                binding = telegram_store.get_binding(chat_id)
                if (binding is None and not telegram_store.is_chat_allowed(chat_id)) \
                        or _foreign_binding(binding, workspace):
                    return _err(f"chat {chat_key!r} is not bound on the telegram connector in this "
                                "workspace; ask the operator to bind it")
            return _ok({"sent": 1 if tg_send(token, chat_id, text) else 0})
        return _ok({"sent": tg_notify(workspace, text)})

    from connectors.channels import notify, registry
    spec = registry.get(channel)
    if spec is None:
        return _err(f"unknown channel: {channel!r}; expected one of {', '.join(registry.names() + ['telegram'])}")
    # The bot serving this workspace: its own, else the default's.
    service = registry.effective_service(channel, workspace) or spec.service
    store = service.store
    if not store.is_configured(*service.required_fields):
        return _err(f"{channel} is not configured on the Connectors page")
    if chat_key:
        key = str(chat_key).strip()
        binding = store.get_binding(key)
        if not store.is_allowed(key) and binding is None:
            return _err(f"chat {key!r} is not bound on the {channel} connector; ask the operator to bind it")
        # A chat bound to another workspace is that workspace's, not this run's.
        if _scoped(workspace) and _foreign_binding(binding, workspace):
            return _err(f"chat {key!r} is not bound on the {channel} connector in this workspace; "
                        "ask the operator to bind it")
        sent = 1 if notify.send_text_sync(channel, key, text, service=service) else 0
        return _ok({"sent": sent, "chat_key": key})
    sent = notify.notify_workspace(channel, workspace, text)
    return _ok({"sent": sent, "workspace": workspace})


CONNECTOR_TOOLS = [channel_send]

__all__ = ["channel_send", "CONNECTOR_TOOLS"]
