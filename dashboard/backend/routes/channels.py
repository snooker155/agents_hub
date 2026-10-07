"""
Chat channels API, one set of routes for every registered channel.

Endpoints (``<name>`` is slack, discord, teams or mail), each for one
workspace (``?workspace=``, the default workspace when omitted):
- GET    /api/channels                       — the channels and their config fields
- GET    /api/channels/<name>/config         — the bot in effect there: public config
                                               (secrets as has_* flags), enabled,
                                               allowed, running, identity, and
                                               ``source``: "here" (the workspace
                                               defines its own bot) or "default"
- PUT    /api/channels/<name>/config         — set fields / clear secrets / enabled /
                                               allowlist (restarts the loop); on a
                                               workspace other than the default this
                                               defines the workspace's own bot
- DELETE /api/channels/<name>/config         — drop a workspace's own bot (stops its
                                               loop), so it uses the default's again
- POST   /api/channels/<name>/test           — verify the credentials in effect there
- GET    /api/channels/<name>/status         — running, last_poll, last_error, identity
- GET    /api/channels/<name>/bindings       — chat → agent/flow bindings (enriched)
- POST   /api/channels/<name>/bindings       — bind a chat to a workspace (+ agent/flow)
- DELETE /api/channels/<name>/bindings/<key> — remove a binding
- POST   /api/channels/<name>/send           — send a message as the bot (debug)

Bots per workspace (docs/connectors.md "Connectors per workspace"): the
default workspace's bot serves every workspace and its chats may be bound to
any. A workspace's own bot serves that workspace only, so a binding of it
names that workspace or none. Asked for a workspace that has no bot of its
own, the bindings routes work on the default's bot, limited to the chats
bound to that workspace.

The same contract as ``routes/telegram.py``: a chat's workspace binding can
only be created here, never from an inbound command, and the allowlist is
what lets a chat talk to the bot at all. Channel-specific inbound webhooks
(Slack Events, Teams messages) live in their own route modules.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import registry as agent_registry
from common.session_broker import notify_change
from connectors.channels import registry as channels
from connectors.channels.store import DEFAULT_WORKSPACE
from workspace import get_workspace_folder


router = APIRouter(prefix="/api/channels", tags=["channels"])


class ChannelConfigUpdate(BaseModel):
    #: Field values to merge; an empty string for a secret means "keep".
    config: Optional[dict[str, Any]] = None
    #: Secret fields to empty.
    clear: Optional[list[str]] = None
    enabled: Optional[bool] = None
    #: None keeps the allowlist; a list (including []) replaces it.
    allowed: Optional[list[str]] = None


class BindingCreate(BaseModel):
    chat_key: str
    #: Optional when asked as a workspace (``?workspace=``): that workspace.
    workspace: Optional[str] = None
    agent_id: Optional[str] = None
    flow_id: Optional[str] = None
    title: Optional[str] = None


class SendRequest(BaseModel):
    chat_key: str
    text: str


def _spec(name: str):
    spec = channels.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {name!r}")
    return spec


def _ws(workspace: Optional[str]) -> str:
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace or "") or DEFAULT_WORKSPACE
    if ws != DEFAULT_WORKSPACE and get_workspace_folder(ws) is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    return ws


def _bot(spec, ws: str):
    """The service of the bot in effect in ``ws`` and whether it is the
    workspace's own: (service, own)."""
    if ws != DEFAULT_WORKSPACE and spec.store.defines(ws):
        svc = channels.service_for(spec.name, ws)
        if svc is not None:
            return svc, True
    return spec.service, ws == DEFAULT_WORKSPACE


def _flow_name(flow_id: str) -> Optional[str]:
    if not flow_id:
        return None
    try:
        from flow import store as flow_store
        flow = flow_store.get_flow(flow_id)
        return flow.get("name") if flow else None
    except Exception:  # noqa: BLE001
        return None


def _enriched(b: dict[str, Any]) -> dict[str, Any]:
    out = dict(b)
    try:
        spec = agent_registry.get_agent(b.get("agent_id") or "")
        out["agent_name"] = spec.name if spec else None
    except Exception:  # noqa: BLE001
        out["agent_name"] = None
    out["flow_name"] = _flow_name(b.get("flow_id") or "")
    return out


def _visible(binding: Optional[dict[str, Any]], ws: str, own: bool) -> bool:
    """Whether a binding of the bot in effect in ``ws`` belongs to ``ws``:
    every binding of the default's bot asked as the default, or of a
    workspace's own bot; on the default's bot asked as another workspace,
    only the chats bound there."""
    if binding is None:
        return False
    if own:
        return True
    return str(binding.get("workspace") or "") == ws


def _config_payload(spec, ws: str) -> dict[str, Any]:
    svc, own = _bot(spec, ws)
    st = svc.status
    store = svc.store
    out = {
        "name": spec.name,
        "workspace": ws,
        # "here": this workspace defines its own bot; "default": inherited.
        "source": "here" if (own or ws == DEFAULT_WORKSPACE) else DEFAULT_WORKSPACE,
        "config": store.public_config(),
        "enabled": store.is_enabled(),
        "configured": svc.configured(),
        "allowed": store.get_allowed(),
        "running": svc.is_running(),
        "identity": st.get("identity"),
        "inbound_url": spec.inbound_url_for(svc.workspace),
        "has_loop": spec.has_loop,
    }
    if ws == DEFAULT_WORKSPACE:
        out["defined_in"] = [DEFAULT_WORKSPACE, *spec.store.defined_workspaces()]
    return out


@router.get("")
async def list_channels():
    return [s.to_dict() for s in channels.all_channels()]


@router.get("/{name}/config")
async def get_config(name: str, workspace: Optional[str] = None):
    return _config_payload(_spec(name), _ws(workspace))


@router.put("/{name}/config")
async def update_config(name: str, data: ChannelConfigUpdate, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    from common import isolation
    try:
        isolation.ensure_not_isolated(ws, "a chat bot of its own")
    except isolation.IsolationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    # The workspace's own document: writing it defines the workspace's bot.
    store = spec.store.for_workspace(ws)
    if data.config is not None or data.clear:
        known = {f.key for f in spec.fields}
        unknown = [k for k in (data.config or {}) if k not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown config fields: {', '.join(unknown)}")
        store.set_config(data.config or {}, clear=data.clear or [])
    if data.enabled is not None:
        store.set_enabled(bool(data.enabled))
    if data.allowed is not None:
        store.set_allowed(data.allowed)
    if ws != DEFAULT_WORKSPACE and not spec.store.defines(ws):
        # Nothing to change was sent: still a definition, an empty bot.
        store.set_config({})

    # Re-sync the loop so the change takes effect now; the supervisor would
    # otherwise pick it up on its next tick.
    await channels.resync(spec.name, ws)
    notify_change(f"channel_{spec.name}", workspace=ws)
    return _config_payload(spec, ws)


@router.delete("/{name}/config")
async def remove_config(name: str, workspace: Optional[str] = None):
    """The workspace stops defining its own bot: its loop stops, its
    document (config, allowlist, bindings) is removed, and the workspace
    uses the default workspace's bot again."""
    spec = _spec(name)
    ws = _ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        raise HTTPException(status_code=400, detail="The default workspace's bot is cleared "
                                                    "field by field, not removed")
    await channels.drop(spec.name, ws)
    spec.store.remove_workspace(ws)
    notify_change(f"channel_{spec.name}", workspace=ws)
    return _config_payload(spec, ws)


@router.post("/{name}/test")
async def test_channel(name: str, workspace: Optional[str] = None):
    spec = _spec(name)
    svc, _own = _bot(spec, _ws(workspace))
    if not svc.configured():
        return {"ok": False, "error": "not configured"}
    return await svc.test()


@router.get("/{name}/status")
async def get_status(name: str, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    svc, own = _bot(spec, ws)
    st = svc.status
    return {
        "workspace": ws,
        "source": "here" if (own or ws == DEFAULT_WORKSPACE) else DEFAULT_WORKSPACE,
        "running": svc.is_running(),
        "last_poll": st.get("last_poll"),
        "last_error": st.get("last_error"),
        "identity": st.get("identity"),
        "enabled": svc.store.is_enabled(),
        "configured": svc.configured(),
    }


@router.get("/{name}/bindings")
async def list_bindings(name: str, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    svc, own = _bot(spec, ws)
    return [_enriched(b) for b in svc.store.list_bindings() if _visible(b, ws, own)]


@router.post("/{name}/bindings")
async def create_binding(name: str, data: BindingCreate, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    svc, own = _bot(spec, ws)
    # A workspace's bot (its own, or the default's asked as that workspace)
    # binds chats to that workspace only.
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
        isolation.ensure_not_isolated(target, "a chat bound to an outside channel")
    except isolation.IsolationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    key = data.chat_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="chat_key is required")
    store = svc.store
    existing = store.get_binding(key) or {}
    if existing and ws != DEFAULT_WORKSPACE and not _visible(existing, ws, own):
        raise HTTPException(status_code=409, detail="This chat is bound to another workspace")
    binding = store.upsert_binding(
        chat_key=key,
        agent_id=data.agent_id or "",
        flow_id=data.flow_id,
        workspace=target,
        conversation_id=existing.get("conversation_id") or str(uuid.uuid4()),
        title=data.title or existing.get("title"),
    )
    # Binding a chat from the dashboard is the operator saying "this chat may
    # talk to the bot": put it on the allowlist too, so one step is enough.
    allowed = store.get_allowed()
    if key not in allowed:
        store.set_allowed([*allowed, key])
    notify_change(f"channel_{spec.name}", chat_key=key, workspace=ws)
    return _enriched(binding)


@router.delete("/{name}/bindings/{chat_key:path}")
async def delete_binding(name: str, chat_key: str, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    svc, own = _bot(spec, ws)
    if ws != DEFAULT_WORKSPACE and not _visible(svc.store.get_binding(chat_key), ws, own):
        raise HTTPException(status_code=404, detail="Binding not found")
    if not svc.store.remove_binding(chat_key):
        raise HTTPException(status_code=404, detail="Binding not found")
    notify_change(f"channel_{spec.name}", chat_key=chat_key, workspace=ws)
    return {"ok": True}


@router.post("/{name}/send")
async def send_as_bot(name: str, data: SendRequest, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    svc, own = _bot(spec, ws)
    if not svc.configured():
        raise HTTPException(status_code=400, detail=f"{spec.name} is not configured")
    if not (data.text or "").strip():
        raise HTTPException(status_code=400, detail="Empty text")
    key = data.chat_key.strip()
    if ws != DEFAULT_WORKSPACE and not own and not _visible(svc.store.get_binding(key), ws, own):
        raise HTTPException(status_code=404, detail=f"Chat '{key}' is not bound to workspace '{ws}'")
    try:
        with svc.scope():
            await svc.send_text(key, data.text)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported as 502
        raise HTTPException(status_code=502, detail=f"{spec.name} send failed: {exc}")
