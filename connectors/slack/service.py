"""
Slack adapter: Socket Mode (default) or the Events API webhook.

Mirrors ``connectors/telegram/telegram_runner.py`` for the parts every
channel shares (chunked replies, button-as-next-message, attachment
download) and leans on ``connectors.channels.service.ChannelService`` for
the rest (allowlist, commands, the turn itself). Two Slack-specific wrinkles:

- Socket Mode owns its own websocket, opened per connection via
  ``apps.connections.open``; the Events API webhook
  (``dashboard/backend/routes/slack.py``) instead feeds messages in from
  outside, so in ``events`` mode :meth:`_run` just waits on the stop event.
  Both transports route through :meth:`handle_event` and
  :meth:`handle_block_action`, so the dispatch logic lives once.
- Slack retries undelivered events, so inbound events are deduped by
  ``client_msg_id`` (falling back to the envelope/event id) over a short
  in-memory window.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from collections import deque
from typing import Any, Optional

import httpx

from connectors.channels.service import ChannelService
from connectors.channels.turns import TurnResult

log = logging.getLogger("channels.slack")

_API_BASE = "https://slack.com/api"
_MAX_FILE_BYTES = 5 * 1024 * 1024
_SPLIT_LIMIT = 3900
_SEEN_WINDOW = 500

_FENCE_RE = re.compile(r"(```.*?```)", re.S)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _to_mrkdwn(text: str) -> str:
    """``**bold**`` to Slack's ``*bold*``; code fences pass through untouched."""
    parts = _FENCE_RE.split(text or "")
    out = []
    for idx, part in enumerate(parts):
        out.append(part if idx % 2 == 1 else _BOLD_RE.sub(r"*\1*", part))
    return "".join(out)


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


async def _slack_api(token: str, method: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{_API_BASE}/{method}", json=payload or {}, headers=headers)
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {body.get('error') or body}")
    return body


class SlackService(ChannelService):
    name = "slack"
    required_fields = ("bot_token",)

    def __init__(self, store) -> None:
        super().__init__(store)
        self._bot_user_id: Optional[str] = None
        self._seen: deque[str] = deque(maxlen=_SEEN_WINDOW)

    # ── credentials ──────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")
        body = await _slack_api(token, "auth.test")
        self._bot_user_id = body.get("user_id")
        self._status["identity"] = f"@{body.get('user') or self._bot_user_id or 'bot'}"
        self._status["bot_user_id"] = self._bot_user_id
        self._status["team"] = body.get("team")

    # ── transport loop ───────────────────────────────────────────────────────

    async def _run(self) -> None:
        mode = str(self.store.get("mode") or "socket").strip().lower()
        if mode == "events":
            # The Events API webhook (routes/slack.py) feeds handle_event /
            # handle_block_action directly; this task just has to stay alive
            # so the status card reads "running".
            assert self._stop_event is not None
            await self._stop_event.wait()
            return
        await self._run_socket_mode()

    async def _run_socket_mode(self) -> None:
        import websockets

        app_token = self.store.get("app_token")
        if not app_token:
            raise RuntimeError("app_token not configured for socket mode")
        opened = await _slack_api(app_token, "apps.connections.open")
        url = opened.get("url")
        if not url:
            raise RuntimeError("apps.connections.open returned no url")

        async with websockets.connect(url) as ws:
            while not self.stopping():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                except asyncio.TimeoutError:
                    continue
                except Exception:  # noqa: BLE001 - the socket died, let the base class reconnect
                    return
                try:
                    envelope = json.loads(raw)
                except Exception:  # noqa: BLE001 - not JSON, ignore
                    continue

                envelope_id = envelope.get("envelope_id")
                if envelope_id:
                    try:
                        await ws.send(json.dumps({"envelope_id": envelope_id}))
                    except Exception:  # noqa: BLE001 - best-effort ack
                        log.debug("slack envelope ack failed", exc_info=True)

                etype = envelope.get("type")
                self._touch()
                if etype == "disconnect":
                    return
                if etype == "events_api":
                    payload = envelope.get("payload") or {}
                    event = payload.get("event") or {}
                    event_id = payload.get("event_id")
                    asyncio.create_task(self._safe_handle_event(event, event_id=event_id))
                elif etype == "interactive":
                    payload = envelope.get("payload") or {}
                    if payload.get("type") == "block_actions":
                        asyncio.create_task(self._safe_handle_block_action(payload))

    async def _safe_handle_event(self, event: dict[str, Any], *, event_id: Optional[str] = None) -> None:
        try:
            await self.handle_event(event, event_id=event_id)
        except Exception as exc:  # noqa: BLE001 - keep the socket loop alive
            log.warning("slack event handling failed: %s", exc)
            self._set_error(exc)

    async def _safe_handle_block_action(self, payload: dict[str, Any]) -> None:
        try:
            await self.handle_block_action(payload)
        except Exception as exc:  # noqa: BLE001 - keep the socket loop alive
            log.warning("slack block action handling failed: %s", exc)
            self._set_error(exc)

    # ── dedupe ───────────────────────────────────────────────────────────────

    def _already_seen(self, key: Optional[str]) -> bool:
        if not key:
            return False
        if key in self._seen:
            return True
        self._seen.append(key)
        return False

    # ── inbound dispatch (shared by Socket Mode and the Events API webhook) ──

    async def handle_event(self, event: dict[str, Any], *, event_id: Optional[str] = None) -> None:
        dedupe_key = event.get("client_msg_id") or event_id
        if self._already_seen(dedupe_key):
            return

        etype = event.get("type")
        if etype not in ("message", "app_mention"):
            return
        if etype == "message" and event.get("subtype") not in (None, "file_share"):
            return
        if event.get("bot_id") or (self._bot_user_id and event.get("user") == self._bot_user_id):
            return

        channel = event.get("channel")
        if not channel:
            return

        text = str(event.get("text") or "")
        channel_type = event.get("channel_type")
        is_dm = (channel_type == "im") if channel_type else str(channel).startswith("D")
        mentioned = etype == "app_mention"
        if self._bot_user_id and f"<@{self._bot_user_id}>" in text:
            mentioned = True
            text = text.replace(f"<@{self._bot_user_id}>", "").strip()
        if not is_dm and not mentioned:
            return

        thread_ts = event.get("thread_ts") or event.get("ts")
        attachments = await self._download_files(event.get("files") or [])
        await self.handle_message(channel, text, attachments=attachments, thread_ts=thread_ts)

    async def handle_block_action(self, payload: dict[str, Any]) -> None:
        actions = payload.get("actions") or []
        if not actions:
            return
        action = actions[0]
        value = action.get("value")
        channel = (payload.get("channel") or {}).get("id")
        if not channel or value is None:
            return
        label = ((action.get("text") or {}).get("text") or "").strip()
        message = payload.get("message") or {}
        thread_ts = message.get("thread_ts") or message.get("ts")

        await self._safe_send(channel, f"➡️ {label or value}", thread_ts=thread_ts)
        await self.handle_message(channel, str(value), thread_ts=thread_ts)

    async def _download_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        token = self.store.get("bot_token")
        out: list[dict[str, Any]] = []
        for f in files:
            url = f.get("url_private_download") or f.get("url_private")
            if not url or not token:
                continue
            size = int(f.get("size") or 0)
            if size and size > _MAX_FILE_BYTES:
                continue
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                    resp.raise_for_status()
                    data = resp.content
            except Exception:  # noqa: BLE001 - one bad download must not drop the message
                continue
            if len(data) > _MAX_FILE_BYTES:
                continue
            out.append({
                "filename": f.get("name") or f"slack_file_{(f.get('id') or uuid.uuid4().hex[:8])}",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mime_type": f.get("mimetype"),
                "store_to_workspace": True,
            })
        return out

    # ── outbound ─────────────────────────────────────────────────────────────

    async def _post_message(self, chat_key: str, text: str, *,
                            blocks: Optional[list[dict[str, Any]]] = None,
                            thread_ts: Optional[str] = None) -> None:
        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")
        payload: dict[str, Any] = {"channel": chat_key, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        await _slack_api(token, "chat.postMessage", payload)

    async def send_text(self, chat_key: str, text: str, *,
                        thread_ts: Optional[str] = None, **kwargs: Any) -> None:
        for chunk in _split(_to_mrkdwn(text or "")):
            await self._post_message(chat_key, chunk, thread_ts=thread_ts)

    async def send_typing(self, chat_key: str) -> None:
        return None

    async def send_result(self, chat_key: str, result: TurnResult, *,
                          thread_ts: Optional[str] = None, **kwargs: Any) -> None:
        payload = result.response_obj
        if isinstance(payload, dict) and payload.get("kind") == "buttons":
            buttons = payload.get("buttons") or []
            if buttons:
                elements = []
                for idx, b in enumerate(buttons):
                    label = str(b.get("label") or b.get("text") or "")
                    value = b.get("value")
                    elements.append({
                        "type": "button",
                        "text": {"type": "plain_text", "text": label or " "},
                        "value": str(value if value is not None else label),
                        "action_id": f"hub_btn_{idx}",
                    })
                blocks: list[dict[str, Any]] = []
                text = (payload.get("text") or payload.get("fallback_text") or result.text or "").strip()
                if text:
                    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _to_mrkdwn(text)}})
                blocks.append({"type": "actions", "elements": elements})
                await self._post_message(chat_key, text or " ", blocks=blocks, thread_ts=thread_ts)
                return
        await self.send_text(chat_key, result.reply, thread_ts=thread_ts)

    def send_text_sync(self, chat_key: str, text: str) -> None:
        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")
        headers = {"Authorization": f"Bearer {token}"}
        for chunk in _split(_to_mrkdwn(text or "")):
            resp = httpx.post(
                f"{_API_BASE}/chat.postMessage",
                json={"channel": chat_key, "text": chunk},
                headers=headers, timeout=30.0,
            )
            body = resp.json()
            if not body.get("ok"):
                raise RuntimeError(f"Slack chat.postMessage failed: {body.get('error') or body}")


__all__ = ["SlackService", "_to_mrkdwn", "_split", "_slack_api"]
