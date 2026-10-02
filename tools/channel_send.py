"""
channel_send — post a message into a chat on one of the chat channels.

The notify_user shape over Slack, Discord, Microsoft Teams or mail: the
destination is a chat the operator bound on the Connectors page (so the
channel is one the hub already talks on), the payload is agent-written
text, which is why ``tools/capabilities.py`` classifies it CAN_EXFILTRATE.
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
            from connectors.telegram import telegram_store
            from connectors.telegram.notify import _send_text as tg_send
        except Exception as exc:  # noqa: BLE001
            return _err(f"telegram unavailable: {exc}")
        if chat_key:
            token = telegram_store.get_token()
            if not token:
                return _err("telegram is not configured")
            return _ok({"sent": 1 if tg_send(token, int(chat_key), text) else 0})
        return _ok({"sent": tg_notify(workspace, text)})

    from connectors.channels import notify, registry
    spec = registry.get(channel)
    if spec is None:
        return _err(f"unknown channel: {channel!r}; expected one of {', '.join(registry.names() + ['telegram'])}")
    if not spec.store.is_configured(*spec.service.required_fields):
        return _err(f"{channel} is not configured on the Connectors page")
    if chat_key:
        key = str(chat_key).strip()
        if not spec.store.is_allowed(key) and spec.store.get_binding(key) is None:
            return _err(f"chat {key!r} is not bound on the {channel} connector; ask the operator to bind it")
        sent = 1 if notify.send_text_sync(channel, key, text) else 0
        return _ok({"sent": sent, "chat_key": key})
    sent = notify.notify_workspace(channel, workspace, text)
    return _ok({"sent": sent, "workspace": workspace})


CONNECTOR_TOOLS = [channel_send]

__all__ = ["channel_send", "CONNECTOR_TOOLS"]
