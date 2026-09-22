"""
Telegram integration API.

Endpoints:
- GET    /api/telegram/config           — { enabled, has_token, bot_username, allowed_chat_ids }
- PUT    /api/telegram/config           — set token / enabled flag / chat allowlist (restarts poller)
- POST   /api/telegram/test             — verify the configured token via getMe
- GET    /api/telegram/status           — poller running, last poll, last error
- GET    /api/telegram/bindings         — list chat→agent bindings (enriched)
- POST   /api/telegram/bindings         — bind a chat to a workspace (+ agent/flow)
- DELETE /api/telegram/bindings/{cid}   — remove a binding
- POST   /api/telegram/send             — send a message as the bot to a chat

A chat's workspace binding can only be created here, from the dashboard — an
inbound Telegram command cannot bind a chat to a workspace itself (see
connectors/telegram/telegram_runner.py). Combined with the allowlist below,
this means a Telegram user who finds the bot can neither talk to it nor grant
themselves access to a workspace without an operator acting first.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import registry
from common.session_broker import notify_change
from connectors.telegram.telegram_runner import TelegramAPI, service as tg_service
from connectors.telegram import telegram_store
from workspace import get_workspace_folder


router = APIRouter(prefix="/api/telegram", tags=["telegram"])


# ── Models ───────────────────────────────────────────────────────────────────


class TelegramConfigResponse(BaseModel):
    enabled: bool
    has_token: bool
    bot_username: Optional[str] = None
    running: bool
    # Chat ids allowed to talk to the bot. Empty means "reject everyone" once a
    # token is configured — an operator opts chats in explicitly.
    allowed_chat_ids: list[int] = []


class TelegramConfigUpdate(BaseModel):
    bot_token: Optional[str] = None  # write-only; None = keep existing
    enabled: Optional[bool] = None
    clear_token: bool = False
    # None = keep existing allowlist; a list (including []) replaces it.
    allowed_chat_ids: Optional[list[int]] = None


class TelegramStatusResponse(BaseModel):
    running: bool
    last_poll: Optional[str] = None
    last_error: Optional[str] = None
    bot_username: Optional[str] = None
    has_token: bool
    enabled: bool


class BindingResponse(BaseModel):
    chat_id: int
    agent_id: str = ""
    agent_name: Optional[str] = None
    flow_id: Optional[str] = None
    flow_name: Optional[str] = None
    workspace: Optional[str] = None
    conversation_id: Optional[str] = None
    title: Optional[str] = None
    created_at: Optional[str] = None
    last_message_at: Optional[str] = None


class BindingCreate(BaseModel):
    """Bind a Telegram chat to a workspace, from the dashboard.

    This is the only way to set a binding's workspace: an inbound Telegram
    command cannot do it (see telegram_runner._handle_command's /workspace
    handling). agent_id and flow_id are optional and mutually exclusive; leave
    both unset to just grant the workspace and let the chat pick a target
    later with /agent or /flow.
    """
    chat_id: int
    workspace: str
    agent_id: Optional[str] = None
    flow_id: Optional[str] = None
    title: Optional[str] = None


class SendRequest(BaseModel):
    chat_id: int
    text: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _flow_name(flow_id: str) -> Optional[str]:
    """Look up a flow's display name via flow_store."""
    if not flow_id:
        return None
    try:
        from flow import store as flow_store
        flow = flow_store.get_flow(flow_id)
        return flow.get("name") if flow else None
    except Exception:
        return None


def _enriched_binding(b: dict) -> dict:
    out = dict(b)
    try:
        spec = registry.get_agent(b.get("agent_id") or "")
        out["agent_name"] = spec.name if spec else None
    except Exception:
        out["agent_name"] = None
    out["flow_name"] = _flow_name(b.get("flow_id") or "")
    return out


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/config", response_model=TelegramConfigResponse)
async def get_config():
    return TelegramConfigResponse(
        enabled=telegram_store.is_enabled(),
        has_token=telegram_store.has_token(),
        bot_username=tg_service.status.get("bot_username"),
        running=tg_service.is_running(),
        allowed_chat_ids=telegram_store.get_allowed_chat_ids(),
    )


@router.put("/config", response_model=TelegramConfigResponse)
async def update_config(data: TelegramConfigUpdate):
    if data.clear_token:
        telegram_store.set_token(None)
    elif data.bot_token is not None:
        token = data.bot_token.strip()
        if token:
            telegram_store.set_token(token)
        # Empty string with clear_token=False is treated as "no change" to avoid
        # accidentally wiping the token from a form that didn't load it.

    if data.enabled is not None:
        telegram_store.set_enabled(bool(data.enabled))

    if data.allowed_chat_ids is not None:
        telegram_store.set_allowed_chat_ids(data.allowed_chat_ids)

    # Re-sync the service so the change takes effect immediately.
    if telegram_store.is_enabled() and telegram_store.has_token():
        await tg_service.restart()
    else:
        await tg_service.stop()

    notify_change("telegram")
    return TelegramConfigResponse(
        enabled=telegram_store.is_enabled(),
        has_token=telegram_store.has_token(),
        bot_username=tg_service.status.get("bot_username"),
        running=tg_service.is_running(),
        allowed_chat_ids=telegram_store.get_allowed_chat_ids(),
    )


@router.post("/test")
async def test_token():
    """Verify the saved token by calling getMe. Does not change running state."""
    token = telegram_store.get_token()
    if not token:
        return {"ok": False, "error": "No token configured"}
    try:
        me = await TelegramAPI(token).get_me()
        return {"ok": True, "bot": me}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@router.get("/status", response_model=TelegramStatusResponse)
async def get_status():
    st = tg_service.status
    return TelegramStatusResponse(
        running=tg_service.is_running(),
        last_poll=st.get("last_poll"),
        last_error=st.get("last_error"),
        bot_username=st.get("bot_username"),
        has_token=telegram_store.has_token(),
        enabled=telegram_store.is_enabled(),
    )


@router.get("/bindings", response_model=list[BindingResponse])
async def list_bindings():
    return [BindingResponse(**_enriched_binding(b)) for b in telegram_store.list_bindings()]


@router.post("/bindings", response_model=BindingResponse)
async def create_binding(data: BindingCreate):
    """Bind a chat to a workspace (and optionally an agent or flow).

    The operator-only counterpart to the removed inbound `/workspace` binding:
    a chat gets access to a workspace by an operator calling this route, not by
    asking the bot for it.
    """
    if get_workspace_folder(data.workspace) is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{data.workspace}' not found")
    if data.agent_id and data.flow_id:
        raise HTTPException(status_code=400, detail="Set agent_id or flow_id, not both")

    existing = telegram_store.get_binding(data.chat_id) or {}
    binding = telegram_store.upsert_binding(
        chat_id=data.chat_id,
        agent_id=data.agent_id or "",
        flow_id=data.flow_id,
        workspace=data.workspace,
        conversation_id=existing.get("conversation_id") or str(uuid.uuid4()),
        title=data.title or existing.get("title"),
    )
    notify_change("telegram", chat_id=data.chat_id)
    return BindingResponse(**_enriched_binding(binding))


@router.delete("/bindings/{chat_id}")
async def delete_binding(chat_id: int):
    removed = telegram_store.remove_binding(chat_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Binding not found")
    notify_change("telegram", chat_id=chat_id)
    return {"ok": True}


@router.post("/send")
async def send_as_bot(data: SendRequest):
    """Send a text message back to a Telegram chat as the bot (debug surface)."""
    token = telegram_store.get_token()
    if not token:
        raise HTTPException(status_code=400, detail="No Telegram token configured")
    if not (data.text or "").strip():
        raise HTTPException(status_code=400, detail="Empty text")
    try:
        result = await TelegramAPI(token).send_message(int(data.chat_id), data.text)
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Telegram send failed: {exc}")
