"""
Discord adapter: the Gateway (websocket) for inbound, the REST API for outbound.

Structurally the same shape as ``connectors/slack/service.py``: a background
task owns the transport, every inbound message or button press funnels
through :class:`connectors.channels.service.ChannelService`'s allowlist,
commands and turn dispatch. Discord has no inbound webhook (there is no
``dashboard/backend/routes/discord.py``), so unlike Slack this channel has
exactly one transport.

The Gateway needs a heartbeat on its own cadence (``HELLO``'s
``heartbeat_interval``) independent of message traffic, and supports
resuming a dropped connection with the last ``session_id`` and sequence
number rather than re-identifying from scratch; both are handled inside
:meth:`_run_gateway`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

import httpx

from connectors.channels.service import ChannelService
from connectors.channels.turns import TurnResult

log = logging.getLogger("channels.discord")

_API_BASE = "https://discord.com/api/v10"
_MAX_FILE_BYTES = 5 * 1024 * 1024
_SPLIT_LIMIT = 1900

# GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
_INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)

_MENTION_RE = re.compile(r"<@!?([^>]+)>")


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


async def _discord_request(token: str, method: str, path: str, *,
                           json_body: Optional[dict[str, Any]] = None,
                           retry: bool = True) -> dict[str, Any]:
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, f"{_API_BASE}{path}", headers=headers, json=json_body)
    if resp.status_code == 429 and retry:
        try:
            retry_after = float((resp.json() or {}).get("retry_after") or 1.0)
        except Exception:  # noqa: BLE001
            retry_after = 1.0
        await asyncio.sleep(retry_after)
        return await _discord_request(token, method, path, json_body=json_body, retry=False)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


class DiscordService(ChannelService):
    name = "discord"
    required_fields = ("bot_token",)

    def __init__(self, store) -> None:
        super().__init__(store)
        self._bot_user_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._resume_gateway_url: Optional[str] = None
        self._seq: Optional[int] = None

    # ── credentials ──────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")
        me = await _discord_request(token, "GET", "/users/@me")
        self._bot_user_id = me.get("id")
        username = me.get("username") or "bot"
        discriminator = str(me.get("discriminator") or "0")
        identity = f"{username}#{discriminator}" if discriminator and discriminator != "0" else f"@{username}"
        self._status["identity"] = identity
        self._status["bot_user_id"] = self._bot_user_id

    # ── gateway loop ─────────────────────────────────────────────────────────

    def _identify_payload(self, token: str) -> dict[str, Any]:
        return {
            "op": 2,
            "d": {
                "token": token,
                "intents": _INTENTS,
                "properties": {"os": "linux", "browser": "agents_hub", "device": "agents_hub"},
            },
        }

    def _resume_payload(self, token: str) -> dict[str, Any]:
        return {"op": 6, "d": {"token": token, "session_id": self._session_id, "seq": self._seq}}

    async def _run(self) -> None:
        await self._run_gateway()

    async def _run_gateway(self) -> None:
        import websockets

        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")

        if self._session_id and self._resume_gateway_url:
            url = self._resume_gateway_url
        else:
            gw = await _discord_request(token, "GET", "/gateway/bot")
            url = gw.get("url")
            if not url:
                raise RuntimeError("gateway/bot returned no url")

        ws_url = f"{url}?v=10&encoding=json"
        heartbeat_task: Optional[asyncio.Task] = None
        try:
            async with websockets.connect(ws_url) as ws:
                while not self.stopping():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:  # noqa: BLE001 - the socket died, let the base class reconnect
                        return
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue

                    op = msg.get("op")
                    data = msg.get("d")
                    seq = msg.get("s")
                    if seq is not None:
                        self._seq = seq
                    self._touch()

                    if op == 10:  # HELLO
                        interval = float((data or {}).get("heartbeat_interval") or 41250) / 1000.0
                        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws, interval))
                        if self._session_id and self._resume_gateway_url:
                            await ws.send(json.dumps(self._resume_payload(token)))
                        else:
                            await ws.send(json.dumps(self._identify_payload(token)))
                    elif op == 7:  # RECONNECT
                        return
                    elif op == 9:  # INVALID_SESSION
                        self._session_id = None
                        self._resume_gateway_url = None
                        return
                    elif op == 0:  # Dispatch
                        t = msg.get("t")
                        if t == "READY":
                            self._session_id = (data or {}).get("session_id")
                            self._resume_gateway_url = (data or {}).get("resume_gateway_url")
                            user = (data or {}).get("user") or {}
                            if user.get("id"):
                                self._bot_user_id = user["id"]
                        elif t == "MESSAGE_CREATE":
                            author = (data or {}).get("author") or {}
                            if not author.get("bot"):
                                asyncio.create_task(self._safe_handle_message(data or {}))
                        elif t == "INTERACTION_CREATE":
                            asyncio.create_task(self._safe_handle_interaction(data or {}))
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws: Any, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({"op": 1, "d": self._seq}))
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - the main loop notices the dead socket
            return

    # ── inbound dispatch ─────────────────────────────────────────────────────

    async def _safe_handle_message(self, message: dict[str, Any]) -> None:
        try:
            await self.handle_discord_message(message)
        except Exception as exc:  # noqa: BLE001 - keep the gateway loop alive
            log.warning("discord message handling failed: %s", exc)
            self._set_error(exc)

    async def handle_discord_message(self, message: dict[str, Any]) -> None:
        channel_id = message.get("channel_id")
        if not channel_id:
            return
        is_dm = not message.get("guild_id")
        text = str(message.get("content") or "")

        mentioned = any(
            str(m.get("id")) == str(self._bot_user_id) for m in (message.get("mentions") or [])
        )
        ref = message.get("referenced_message") or {}
        if not mentioned and ref and str((ref.get("author") or {}).get("id") or "") == str(self._bot_user_id):
            mentioned = True
        if not is_dm and not mentioned:
            return

        if self._bot_user_id:
            text = _MENTION_RE.sub(
                lambda m: "" if m.group(1) == str(self._bot_user_id) else m.group(0), text
            ).strip()

        attachments = await self._download_attachments(message.get("attachments") or [])
        await self.handle_message(channel_id, text, attachments=attachments, reply_to=message.get("id"))

    async def _download_attachments(self, atts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for a in atts:
            url = a.get("url")
            if not url:
                continue
            size = int(a.get("size") or 0)
            if size and size > _MAX_FILE_BYTES:
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
                "filename": a.get("filename") or f"discord_file_{uuid.uuid4().hex[:8]}",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mime_type": a.get("content_type"),
                "store_to_workspace": True,
            })
        return out

    async def _safe_handle_interaction(self, interaction: dict[str, Any]) -> None:
        try:
            await self.handle_interaction(interaction)
        except Exception as exc:  # noqa: BLE001 - keep the gateway loop alive
            log.warning("discord interaction handling failed: %s", exc)
            self._set_error(exc)

    async def handle_interaction(self, interaction: dict[str, Any]) -> None:
        if interaction.get("type") != 3:  # MESSAGE_COMPONENT
            return
        token = self.store.get("bot_token")
        interaction_id = interaction.get("id")
        interaction_token = interaction.get("token")
        if token and interaction_id and interaction_token:
            try:
                await _discord_request(
                    token, "POST", f"/interactions/{interaction_id}/{interaction_token}/callback",
                    json_body={"type": 6},  # DEFERRED_UPDATE_MESSAGE
                )
            except Exception:  # noqa: BLE001 - the turn reply still goes out
                pass

        data = interaction.get("data") or {}
        custom_id = str(data.get("custom_id") or "")
        value = custom_id.split(":", 1)[1] if custom_id.startswith("hub_btn:") else custom_id
        message = interaction.get("message") or {}
        channel_id = interaction.get("channel_id") or message.get("channel_id")
        if not channel_id or not value:
            return

        label = None
        for row in message.get("components") or []:
            for comp in row.get("components") or []:
                if comp.get("custom_id") == custom_id:
                    label = comp.get("label")

        await self._safe_send(channel_id, f"➡️ {label or value}")
        await self.handle_message(channel_id, value, reply_to=message.get("id"))

    # ── outbound ─────────────────────────────────────────────────────────────

    async def send_text(self, chat_key: str, text: str, *,
                        reply_to: Optional[str] = None, **kwargs: Any) -> None:
        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")
        chunks = _split(text or "")
        for idx, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"content": chunk}
            if reply_to and idx == 0:
                payload["message_reference"] = {"message_id": reply_to}
            await _discord_request(token, "POST", f"/channels/{chat_key}/messages", json_body=payload)

    async def send_typing(self, chat_key: str) -> None:
        token = self.store.get("bot_token")
        if not token:
            return
        try:
            await _discord_request(token, "POST", f"/channels/{chat_key}/typing")
        except Exception:  # noqa: BLE001 - a typing indicator is cosmetic
            pass

    async def send_result(self, chat_key: str, result: TurnResult, *,
                          reply_to: Optional[str] = None, **kwargs: Any) -> None:
        payload = result.response_obj
        if isinstance(payload, dict) and payload.get("kind") == "buttons":
            buttons = payload.get("buttons") or []
            if buttons:
                token = self.store.get("bot_token")
                components: list[dict[str, Any]] = []
                row: dict[str, Any] = {"type": 1, "components": []}
                for b in buttons[:25]:
                    if len(row["components"]) >= 5:
                        components.append(row)
                        row = {"type": 1, "components": []}
                    label = str(b.get("label") or b.get("text") or "")
                    value = b.get("value")
                    row["components"].append({
                        "type": 2, "style": 1,
                        "label": label or " ",
                        "custom_id": f"hub_btn:{value if value is not None else label}",
                    })
                if row["components"]:
                    components.append(row)
                text = (payload.get("text") or payload.get("fallback_text") or result.text or "").strip()
                body: dict[str, Any] = {"content": text or " ", "components": components}
                if reply_to:
                    body["message_reference"] = {"message_id": reply_to}
                try:
                    await _discord_request(token, "POST", f"/channels/{chat_key}/messages", json_body=body)
                    return
                except Exception:  # noqa: BLE001 - fall back to plain text
                    log.warning("discord structured send failed for chat=%s", chat_key, exc_info=True)
        await self.send_text(chat_key, result.reply, reply_to=reply_to)

    def send_text_sync(self, chat_key: str, text: str) -> None:
        token = self.store.get("bot_token")
        if not token:
            raise RuntimeError("bot_token not configured")
        headers = {"Authorization": f"Bot {token}"}
        for chunk in _split(text or ""):
            resp = httpx.post(
                f"{_API_BASE}/channels/{chat_key}/messages",
                json={"content": chunk}, headers=headers, timeout=30.0,
            )
            if resp.status_code == 429:
                try:
                    retry_after = float((resp.json() or {}).get("retry_after") or 1.0)
                except Exception:  # noqa: BLE001
                    retry_after = 1.0
                time.sleep(retry_after)
                resp = httpx.post(
                    f"{_API_BASE}/channels/{chat_key}/messages",
                    json={"content": chunk}, headers=headers, timeout=30.0,
                )
            resp.raise_for_status()


__all__ = ["DiscordService", "_split", "_discord_request"]
