"""Notifications: the alert rules that raise an in-app notification.

    GET/POST/PATCH/DELETE /api/notify/rules          run_failed/spend alert rules

A rule fires into the notification inbox (the bell); there is no other
channel. The inbox itself is served by ``routes/plan.py``.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from notify import store as notify_store

router = APIRouter(tags=["notify"])


# ── shared helpers ───────────────────────────────────────────────────────────

def _workspace(value: Optional[str]) -> str:
    """The workspace this request is about, or a 400 saying there is none."""
    from common.workspace_context import resolve_active_workspace

    ws = resolve_active_workspace(value)
    if not ws:
        raise HTTPException(status_code=400, detail="A workspace is required")
    return ws


# ── alert rules ──────────────────────────────────────────────────────────────

class CreateRule(BaseModel):
    kind: str
    threshold_usd: float = 0.0
    agent_id: Optional[str] = None
    channels: List[str] = ["dashboard"]
    enabled: bool = True


class UpdateRule(BaseModel):
    kind: Optional[str] = None
    threshold_usd: Optional[float] = None
    agent_id: Optional[str] = None
    channels: Optional[List[str]] = None
    enabled: Optional[bool] = None


@router.get("/api/notify/rules")
async def list_rules(workspace: Optional[str] = None):
    ws = _workspace(workspace)
    return {
        "rules": notify_store.list_rules(ws),
        "workspace": ws,
        "kinds": list(notify_store.RULE_KINDS),
        "channels": list(notify_store.CHANNELS),
    }


@router.post("/api/notify/rules", status_code=201)
async def create_rule(body: CreateRule, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    try:
        record = notify_store.create_rule(ws, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"rule": record}


@router.patch("/api/notify/rules/{rule_id}")
async def update_rule(rule_id: str, body: UpdateRule, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        record = notify_store.update_rule(ws, rule_id, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if record is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"rule": record}


@router.delete("/api/notify/rules/{rule_id}")
async def delete_rule(rule_id: str, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    if not notify_store.delete_rule(ws, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True, "id": rule_id}
