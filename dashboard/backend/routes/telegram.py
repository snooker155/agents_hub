"""
Telegram integration API.

Endpoints, each for one workspace (``?workspace=``, the default when omitted):
- GET    /api/telegram/config           — { enabled, has_token, bot_username, allowed_chat_ids,
                                            source: "here" | "default" }
- PUT    /api/telegram/config           — set token / enabled flag / chat allowlist (restarts
                                            poller); on a workspace other than the default this
                                            defines the workspace's own bot
- DELETE /api/telegram/config           — drop a workspace's own bot (stops its poller)
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

Bots per workspace (docs/connectors.md "Connectors per workspace"): the
default workspace's bot serves every workspace. A workspace's own bot
(``connectors/telegram/bots.py``) serves that workspace only, so a binding of
it names that workspace. Asked for a workspace with no bot of its own, the
bindings routes work on the default's bot, limited to the chats bound there.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import registry
from common.session_broker import notify_change
from connectors.telegram.telegram_runner import TelegramAPI, service as tg_service
from connectors.telegram import bots as tg_bots
from connectors.telegram import telegram_store
from workspace import get_workspace_folder

DEFAULT_WORKSPACE = telegram_store.DEFAULT_WORKSPACE


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
    workspace: str = DEFAULT_WORKSPACE
    # "here": the workspace defines its own bot; "default": inherited.
    source: str = "here"
    # On the default workspace: the workspaces with a bot of their own.
    defined_in: Optional[list[str]] = None


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
    workspace: str = DEFAULT_WORKSPACE
    source: str = "here"


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
    #: Optional when asked as a workspace (``?workspace=``): that workspace.
    workspace: Optional[str] = None
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


def _ws(workspace: Optional[str]) -> str:
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace or "") or DEFAULT_WORKSPACE
    if ws != DEFAULT_WORKSPACE and get_workspace_folder(ws) is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    return ws


def _bot(ws: str):
    """The service of the bot in effect in ``ws`` and whether it is the
    workspace's own: (service, own). The default workspace's is its own."""
    if ws != DEFAULT_WORKSPACE and telegram_store.defines(ws):
        svc = tg_bots.service_for(ws)
        if svc is not None:
            return svc, True
    return tg_service, ws == DEFAULT_WORKSPACE


def _visible(binding: Optional[dict], ws: str, own: bool) -> bool:
    """Whether a binding of the bot in effect in ``ws`` belongs to ``ws``."""
    if binding is None:
        return False
    return own or str(binding.get("workspace") or "") == ws


def _config_response(ws: str) -> TelegramConfigResponse:
    svc, own = _bot(ws)
    store = svc.store
    return TelegramConfigResponse(
        enabled=store.is_enabled(),
        has_token=store.has_token(),
        bot_username=svc.status.get("bot_username"),
        running=svc.is_running(),
        allowed_chat_ids=store.get_allowed_chat_ids(),
        workspace=ws,
        source="here" if own else DEFAULT_WORKSPACE,
        defined_in=[DEFAULT_WORKSPACE, *telegram_store.defined_workspaces()]
        if ws == DEFAULT_WORKSPACE else None,
    )


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
async def get_config(workspace: Optional[str] = None):
    return _config_response(_ws(workspace))


@router.put("/config", response_model=TelegramConfigResponse)
async def update_config(data: TelegramConfigUpdate, workspace: Optional[str] = None):
    ws = _ws(workspace)
    from common import isolation
    try:
        isolation.ensure_not_isolated(ws, "a chat bot of its own")
    except isolation.IsolationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    # The workspace's own document: writing it defines the workspace's bot.
    store = telegram_store.for_workspace(ws)
    if data.clear_token:
        store.set_token(None)
    elif data.bot_token is not None:
        token = data.bot_token.strip()
        if token:
            store.set_token(token)
        # Empty string with clear_token=False is treated as "no change" to avoid
        # accidentally wiping the token from a form that didn't load it.

    if data.enabled is not None:
        store.set_enabled(bool(data.enabled))

    if data.allowed_chat_ids is not None:
        store.set_allowed_chat_ids(data.allowed_chat_ids)

    if ws != DEFAULT_WORKSPACE and not telegram_store.defines(ws):
        # Nothing to change was sent: still a definition, an empty bot.
        store.set_enabled(store.is_enabled())

    # Re-sync the service so the change takes effect immediately.
    await tg_bots.resync(ws)

    notify_change("telegram", workspace=ws)
    return _config_response(ws)


@router.delete("/config", response_model=TelegramConfigResponse)
async def remove_config(workspace: Optional[str] = None):
    """The workspace stops defining its own bot: its poller stops, its
    document (token, allowlist, bindings) is removed, and the workspace uses
    the default workspace's bot again."""
    ws = _ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        raise HTTPException(status_code=400, detail="The default workspace's bot is cleared "
                                                    "field by field, not removed")
    await tg_bots.drop(ws)
    telegram_store.remove_workspace(ws)
    notify_change("telegram", workspace=ws)
    return _config_response(ws)


@router.post("/test")
async def test_token(workspace: Optional[str] = None):
    """Verify the saved token by calling getMe. Does not change running state."""
    svc, _own = _bot(_ws(workspace))
    token = svc.store.get_token()
    if not token:
        return {"ok": False, "error": "No token configured"}
    try:
        me = await TelegramAPI(token).get_me()
        return {"ok": True, "bot": me}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@router.get("/status", response_model=TelegramStatusResponse)
async def get_status(workspace: Optional[str] = None):
    ws = _ws(workspace)
    svc, own = _bot(ws)
    st = svc.status
    return TelegramStatusResponse(
        running=svc.is_running(),
        last_poll=st.get("last_poll"),
        last_error=st.get("last_error"),
        bot_username=st.get("bot_username"),
        has_token=svc.store.has_token(),
        enabled=svc.store.is_enabled(),
        workspace=ws,
        source="here" if own else DEFAULT_WORKSPACE,
    )


@router.get("/bindings", response_model=list[BindingResponse])
async def list_bindings(workspace: Optional[str] = None):
    ws = _ws(workspace)
    svc, own = _bot(ws)
    return [BindingResponse(**_enriched_binding(b)) for b in svc.store.list_bindings()
            if _visible(b, ws, own)]


@router.post("/bindings", response_model=BindingResponse)
async def create_binding(data: BindingCreate, workspace: Optional[str] = None):
    """Bind a chat to a workspace (and optionally an agent or flow).

    The operator-only counterpart to the removed inbound `/workspace` binding:
    a chat gets access to a workspace by an operator calling this route, not by
    asking the bot for it.
    """
    ws = _ws(workspace)
    svc, own = _bot(ws)
    target = (data.workspace or "").strip() or (ws if ws != DEFAULT_WORKSPACE else "")
    if not target:
        raise HTTPException(status_code=400, detail="workspace is required")
    if ws != DEFAULT_WORKSPACE and target != ws:
        detail = (f"This bot serves workspace '{ws}' only" if own
                  else f"From workspace '{ws}' a chat can be bound to '{ws}' only")
        raise HTTPException(status_code=400, detail=detail)
    if get_workspace_folder(target) is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{target}' not found")
    if data.agent_id and data.flow_id:
        raise HTTPException(status_code=400, detail="Set agent_id or flow_id, not both")
    from common import isolation
    try:
        isolation.ensure_not_isolated(target, "a Telegram chat")
    except isolation.IsolationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    store = svc.store
    existing = store.get_binding(data.chat_id) or {}
    if existing and ws != DEFAULT_WORKSPACE and not _visible(existing, ws, own):
        raise HTTPException(status_code=409, detail="This chat is bound to another workspace")
    binding = store.upsert_binding(
        chat_id=data.chat_id,
        agent_id=data.agent_id or "",
        flow_id=data.flow_id,
        workspace=target,
        conversation_id=existing.get("conversation_id") or str(uuid.uuid4()),
        title=data.title or existing.get("title"),
    )
    notify_change("telegram", chat_id=data.chat_id, workspace=ws)
    return BindingResponse(**_enriched_binding(binding))


@router.delete("/bindings/{chat_id}")
async def delete_binding(chat_id: int, workspace: Optional[str] = None):
    ws = _ws(workspace)
    svc, own = _bot(ws)
    if ws != DEFAULT_WORKSPACE and not _visible(svc.store.get_binding(chat_id), ws, own):
        raise HTTPException(status_code=404, detail="Binding not found")
    removed = svc.store.remove_binding(chat_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Binding not found")
    notify_change("telegram", chat_id=chat_id, workspace=ws)
    return {"ok": True}


@router.post("/send")
async def send_as_bot(data: SendRequest, workspace: Optional[str] = None):
    """Send a text message back to a Telegram chat as the bot (debug surface)."""
    ws = _ws(workspace)
    svc, own = _bot(ws)
    token = svc.store.get_token()
    if not token:
        raise HTTPException(status_code=400, detail="No Telegram token configured")
    if not (data.text or "").strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if ws != DEFAULT_WORKSPACE and not own and not _visible(svc.store.get_binding(data.chat_id), ws, own):
        raise HTTPException(status_code=404, detail=f"Chat {data.chat_id} is not bound to workspace '{ws}'")
    try:
        result = await TelegramAPI(token).send_message(int(data.chat_id), data.text)
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Telegram send failed: {exc}")
