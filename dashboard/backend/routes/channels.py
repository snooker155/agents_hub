"""
Chat channels API, one set of routes for every registered channel.

Endpoints (``<name>`` is slack, discord, teams or mail):
- GET    /api/channels                       — the channels and their config fields
- GET    /api/channels/<name>/config         — public config (secrets as has_* flags),
                                               enabled, allowed, running, identity
- PUT    /api/channels/<name>/config         — set fields / clear secrets / enabled /
                                               allowlist (restarts the loop)
- POST   /api/channels/<name>/test           — verify the saved credentials
- GET    /api/channels/<name>/status         — running, last_poll, last_error, identity
- GET    /api/channels/<name>/bindings       — chat → agent/flow bindings (enriched)
- POST   /api/channels/<name>/bindings       — bind a chat to a workspace (+ agent/flow)
- DELETE /api/channels/<name>/bindings/<key> — remove a binding
- POST   /api/channels/<name>/send           — send a message as the bot (debug)

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
    workspace: str
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


def _config_payload(spec) -> dict[str, Any]:
    st = spec.service.status
    return {
        "name": spec.name,
        "config": spec.store.public_config(),
        "enabled": spec.store.is_enabled(),
        "configured": spec.store.is_configured(*spec.service.required_fields),
        "allowed": spec.store.get_allowed(),
        "running": spec.service.is_running(),
        "identity": st.get("identity"),
        "inbound_url": spec.inbound_url,
        "has_loop": spec.has_loop,
    }


@router.get("")
async def list_channels():
    return [s.to_dict() for s in channels.all_channels()]


@router.get("/{name}/config")
async def get_config(name: str):
    return _config_payload(_spec(name))


@router.put("/{name}/config")
async def update_config(name: str, data: ChannelConfigUpdate):
    spec = _spec(name)
    if data.config is not None or data.clear:
        known = {f.key for f in spec.fields}
        unknown = [k for k in (data.config or {}) if k not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown config fields: {', '.join(unknown)}")
        spec.store.set_config(data.config or {}, clear=data.clear or [])
    if data.enabled is not None:
        spec.store.set_enabled(bool(data.enabled))
    if data.allowed is not None:
        spec.store.set_allowed(data.allowed)

    # Re-sync the loop so the change takes effect now; the supervisor would
    # otherwise pick it up on its next tick.
    if spec.has_loop:
        if spec.service.wanted():
            await spec.service.restart()
        else:
            await spec.service.stop()
    notify_change(f"channel_{spec.name}")
    return _config_payload(spec)


@router.post("/{name}/test")
async def test_channel(name: str):
    spec = _spec(name)
    if not spec.store.is_configured(*spec.service.required_fields):
        return {"ok": False, "error": "not configured"}
    return await spec.service.test()


@router.get("/{name}/status")
async def get_status(name: str):
    spec = _spec(name)
    st = spec.service.status
    return {
        "running": spec.service.is_running(),
        "last_poll": st.get("last_poll"),
        "last_error": st.get("last_error"),
        "identity": st.get("identity"),
        "enabled": spec.store.is_enabled(),
        "configured": spec.store.is_configured(*spec.service.required_fields),
    }


@router.get("/{name}/bindings")
async def list_bindings(name: str):
    spec = _spec(name)
    return [_enriched(b) for b in spec.store.list_bindings()]


@router.post("/{name}/bindings")
async def create_binding(name: str, data: BindingCreate):
    spec = _spec(name)
    if get_workspace_folder(data.workspace) is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{data.workspace}' not found")
    if data.agent_id and data.flow_id:
        raise HTTPException(status_code=400, detail="Set agent_id or flow_id, not both")
    key = data.chat_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="chat_key is required")
    existing = spec.store.get_binding(key) or {}
    binding = spec.store.upsert_binding(
        chat_key=key,
        agent_id=data.agent_id or "",
        flow_id=data.flow_id,
        workspace=data.workspace,
        conversation_id=existing.get("conversation_id") or str(uuid.uuid4()),
        title=data.title or existing.get("title"),
    )
    # Binding a chat from the dashboard is the operator saying "this chat may
    # talk to the bot": put it on the allowlist too, so one step is enough.
    allowed = spec.store.get_allowed()
    if key not in allowed:
        spec.store.set_allowed([*allowed, key])
    notify_change(f"channel_{spec.name}", chat_key=key)
    return _enriched(binding)


@router.delete("/{name}/bindings/{chat_key:path}")
async def delete_binding(name: str, chat_key: str):
    spec = _spec(name)
    if not spec.store.remove_binding(chat_key):
        raise HTTPException(status_code=404, detail="Binding not found")
    notify_change(f"channel_{spec.name}", chat_key=chat_key)
    return {"ok": True}


@router.post("/{name}/send")
async def send_as_bot(name: str, data: SendRequest):
    spec = _spec(name)
    if not spec.store.is_configured(*spec.service.required_fields):
        raise HTTPException(status_code=400, detail=f"{spec.name} is not configured")
    if not (data.text or "").strip():
        raise HTTPException(status_code=400, detail="Empty text")
    try:
        await spec.service.send_text(data.chat_key.strip(), data.text)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported as 502
        raise HTTPException(status_code=502, detail=f"{spec.name} send failed: {exc}")
