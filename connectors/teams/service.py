"""
Teams adapter: Bot Framework activities over HTTP, no polling.

Teams pushes every message as an activity POSTed to
``dashboard/backend/routes/teams_channel.py``, which verifies the bearer JWT
(``connectors/teams/auth.py``) and hands it to
:meth:`TeamsService.handle_activity`. A reply goes back out the same way
Slack and Telegram do (chunked, see ``connectors/slack/service.py``), except
the destination is the ``serviceUrl`` recorded from the conversation's last
inbound activity rather than a fixed API host, so each chat's cursor
(``store.set_cursor``) remembers it.

``has_loop`` is False on the channel spec (``connectors/teams/__init__.py``):
there is nothing to poll. :meth:`_run` still exists, as a wait on the stop
event, so the base class's lifecycle (status, ``test``) behaves the same as
a channel that does have one, and a stray ``start()`` call is harmless.
"""
from __future__ import annotations

import base64
import logging
import uuid
from typing import Any, Optional

import httpx

from connectors.channels.service import ChannelService
from connectors.channels.turns import TurnResult

from . import auth

log = logging.getLogger("channels.teams")

_MAX_FILE_BYTES = 5 * 1024 * 1024
_SPLIT_LIMIT = 4000
_FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"


def _split(text: str, limit: int = _SPLIT_LIMIT) -> list[str]:
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < 0:
            cut = remaining.rfind("\n", 0, limit)
        if cut < 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut < 0 or cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _is_mention_of(entity: dict[str, Any], bot_id: Optional[str]) -> bool:
    if entity.get("type") != "mention":
        return False
    mentioned = entity.get("mentioned") or {}
    return bool(bot_id) and mentioned.get("id") == bot_id


def strip_mentions(text: str, entities: list[dict[str, Any]], bot_id: Optional[str]) -> str:
    """Drop the ``<at>Bot</at>`` wrapper Teams adds when the bot is @mentioned."""
    out = text or ""
    for ent in entities or []:
        if not _is_mention_of(ent, bot_id):
            continue
        ent_text = ent.get("text") or ""
        if ent_text:
            out = out.replace(ent_text, "")
    return out.strip()


def is_mentioned(entities: list[dict[str, Any]], bot_id: Optional[str]) -> bool:
    return any(_is_mention_of(ent, bot_id) for ent in entities or [])


class TeamsService(ChannelService):
    name = "teams"
    required_fields = ("app_id", "app_password")

    # ── credentials ──────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        app_id = self.store.get("app_id")
        app_password = self.store.get("app_password")
        if not app_id or not app_password:
            raise RuntimeError("app_id/app_password not configured")
        await auth.get_outbound_token(app_id, app_password)
        self._status["identity"] = f"app {app_id}"

    # ── transport loop: nothing to poll, Teams pushes to the webhook ───────────

    async def _run(self) -> None:
        assert self._stop_event is not None
        await self._stop_event.wait()

    # ── inbound ──────────────────────────────────────────────────────────────

    async def handle_activity(self, activity: dict[str, Any]) -> None:
        if (activity.get("type") or "") != "message":
            return
        conversation = activity.get("conversation") or {}
        chat_key = str(conversation.get("id") or "")
        if not chat_key:
            return
        if not self.chat_allowed(chat_key):
            return

        recipient = activity.get("recipient") or {}
        sender = activity.get("from") or {}
        if recipient.get("id") and sender.get("id") == recipient.get("id"):
            return  # the bot's own message, echoed back

        entities = activity.get("entities") or []
        bot_id = recipient.get("id")
        text = strip_mentions(str(activity.get("text") or ""), entities, bot_id)
        conversation_type = str(conversation.get("conversationType") or "")
        if conversation_type != "personal" and not is_mentioned(entities, bot_id):
            return  # a group chat/channel: only react when mentioned

        self.store.set_cursor(f"conv:{chat_key}", {
            "serviceUrl": activity.get("serviceUrl"),
            "conversation": conversation,
            "recipient": recipient,
            "from": sender,
            "activity_id": activity.get("id"),
        })

        attachments = await self._download_attachments(activity.get("attachments") or [])
        self._touch()
        await self.handle_message(
            chat_key, text, attachments=attachments, title=conversation.get("name"),
            reply_to=activity.get("id"),
        )

    async def _download_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for att in attachments:
            if att.get("contentType") != _FILE_DOWNLOAD_INFO:
                continue
            content = att.get("content") or {}
            url = content.get("downloadUrl")
            if not url:
                continue
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.content
            except Exception:  # noqa: BLE001 - one bad download must not drop the message
                continue
            if len(data) > _MAX_FILE_BYTES:
                continue
            out.append({
                "filename": att.get("name") or f"teams_file_{uuid.uuid4().hex[:8]}",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mime_type": content.get("fileType"),
                "store_to_workspace": True,
            })
        return out

    # ── outbound ─────────────────────────────────────────────────────────────

    def _conv_info(self, chat_key: str) -> dict[str, Any]:
        info = self.store.get_cursor(f"conv:{chat_key}")
        if not info:
            raise RuntimeError(f"no known conversation for chat {chat_key!r}; it must message the bot first")
        return info

    def _activity_url(self, info: dict[str, Any], chat_key: str, activity_id: Optional[str]) -> str:
        service_url = str(info.get("serviceUrl") or "").rstrip("/")
        if not service_url:
            raise RuntimeError(f"no serviceUrl recorded for chat {chat_key!r}")
        conv_id = (info.get("conversation") or {}).get("id") or chat_key
        if activity_id:
            return f"{service_url}/v3/conversations/{conv_id}/activities/{activity_id}"
        return f"{service_url}/v3/conversations/{conv_id}/activities"

    def _activity_body(self, info: dict[str, Any], payload: dict[str, Any],
                       activity_id: Optional[str]) -> dict[str, Any]:
        body = {
            **payload,
            "from": info.get("recipient"),
            "recipient": info.get("from"),
            "conversation": info.get("conversation"),
        }
        if activity_id:
            body["replyToId"] = activity_id
        return body

    async def _post_activity(self, chat_key: str, payload: dict[str, Any], *,
                             reply_to: Optional[str] = None) -> None:
        app_id = self.store.get("app_id")
        app_password = self.store.get("app_password")
        if not app_id or not app_password:
            raise RuntimeError("app_id/app_password not configured")
        info = self._conv_info(chat_key)
        activity_id = reply_to or info.get("activity_id")
        url = self._activity_url(info, chat_key, activity_id)
        body = self._activity_body(info, payload, activity_id)
        token = await auth.get_outbound_token(app_id, app_password)
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
        if resp.status_code >= 300:
            raise RuntimeError(f"Teams activity post failed: {resp.status_code} {resp.text[:200]}")

    async def send_text(self, chat_key: str, text: str, *, reply_to: Optional[str] = None,
                        **kwargs: Any) -> None:
        for chunk in _split(text or ""):
            await self._post_activity(chat_key, {"type": "message", "text": chunk}, reply_to=reply_to)

    async def send_typing(self, chat_key: str) -> None:
        try:
            await self._post_activity(chat_key, {"type": "typing"})
        except Exception:  # noqa: BLE001 - a typing indicator is cosmetic
            log.debug("teams typing indicator failed", exc_info=True)

    async def send_result(self, chat_key: str, result: TurnResult, *,
                          reply_to: Optional[str] = None, **kwargs: Any) -> None:
        payload = result.response_obj
        if isinstance(payload, dict) and payload.get("kind") == "buttons":
            buttons = payload.get("buttons") or []
            if buttons:
                text = (payload.get("text") or payload.get("fallback_text") or result.text or "").strip()
                actions = []
                for b in buttons:
                    label = str(b.get("label") or b.get("text") or "")
                    value = b.get("value")
                    actions.append({
                        "type": "imBack",
                        "title": label or str(value),
                        "value": str(value if value is not None else label),
                    })
                attachment = {
                    "contentType": "application/vnd.microsoft.card.hero",
                    "content": {"buttons": actions, **({"text": text} if text else {})},
                }
                await self._post_activity(
                    chat_key, {"type": "message", "text": text, "attachments": [attachment]},
                    reply_to=reply_to,
                )
                return
        await self.send_text(chat_key, result.reply, reply_to=reply_to)

    def send_text_sync(self, chat_key: str, text: str) -> None:
        app_id = self.store.get("app_id")
        app_password = self.store.get("app_password")
        if not app_id or not app_password:
            raise RuntimeError("app_id/app_password not configured")
        info = self._conv_info(chat_key)
        activity_id = info.get("activity_id")
        url = self._activity_url(info, chat_key, activity_id)
        token = auth.get_outbound_token_sync(app_id, app_password)
        headers = {"Authorization": f"Bearer {token}"}
        for chunk in _split(text or ""):
            body = self._activity_body(info, {"type": "message", "text": chunk}, activity_id)
            resp = httpx.post(url, json=body, headers=headers, timeout=30.0)
            if resp.status_code >= 300:
                raise RuntimeError(f"Teams activity post failed: {resp.status_code} {resp.text[:200]}")


__all__ = ["TeamsService", "strip_mentions", "is_mentioned"]
