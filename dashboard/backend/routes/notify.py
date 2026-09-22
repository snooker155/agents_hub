"""Notifications: outbound endpoints, alert rules, and the inbound task webhook.

    GET/POST/PATCH/DELETE /api/notify/endpoints      webhook/slack endpoints
    POST   /api/notify/endpoints/{id}/test           send a test event now
    GET/POST/PATCH/DELETE /api/notify/rules          run_failed/spend alert rules
    GET/POST            /api/notify/inbound-secret   the task webhook's secret
    POST   /api/webhooks/tasks                       file a task from outside

Two things shape this router, both following ``routes/mcp.py``'s lead.

**Nothing secret goes out.** A webhook endpoint's secret leaves a GET as its
last four characters (:func:`notify.store.masked_endpoint`); sending that
masked form back on a PATCH is read as "unchanged", so saving a form nobody
edited cannot overwrite a real secret with dots. The inbound task-webhook
secret goes further: it is never read back at all, only whether one is set.

**The task webhook is fail-closed.** Unlike the node and flow triggers (which
accept an unsigned request when no secret has been configured, to keep their
existing behaviour), an external system filing a task has no other identity
to fall back on: a workspace with no inbound secret configured simply cannot
be posted to. See ``docs/notifications.md`` for the header names and the
signature scheme both directions share.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from notify import store as notify_store
from notify.inbound import seen_delivery, verify_signature

router = APIRouter(tags=["notify"])


# ── shared helpers ───────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()


def _workspace(value: Optional[str]) -> str:
    """The workspace this request is about, or a 400 saying there is none."""
    from common.workspace_context import resolve_active_workspace

    ws = resolve_active_workspace(value)
    if not ws:
        raise HTTPException(status_code=400, detail="A workspace is required")
    return ws


def _require_signed(secret: str, request_body: bytes, request: Request, *, delivery_required: bool) -> None:
    """Verify signature + timestamp, then reject a replayed delivery.

    Raises the matching HTTPException; the caller does not need to inspect
    the result.
    """
    signature = request.headers.get("X-AgentsHub-Signature", "")
    timestamp = request.headers.get("X-AgentsHub-Timestamp", "")
    delivery_id = request.headers.get("X-AgentsHub-Delivery", "")
    if not verify_signature(secret, request_body, signature, timestamp):
        raise HTTPException(status_code=401, detail="Invalid or missing signature")
    if delivery_required and not delivery_id:
        raise HTTPException(status_code=401, detail="Missing X-AgentsHub-Delivery header")
    if delivery_id and seen_delivery(delivery_id):
        raise HTTPException(status_code=409, detail="Delivery already processed")


# ── endpoints (webhook / slack) ──────────────────────────────────────────────

class CreateEndpoint(BaseModel):
    kind: str
    url: str
    secret: Optional[str] = None
    events: List[str] = ["notification"]
    enabled: bool = True


class UpdateEndpoint(BaseModel):
    kind: Optional[str] = None
    url: Optional[str] = None
    secret: Optional[str] = None
    events: Optional[List[str]] = None
    enabled: Optional[bool] = None


@router.get("/api/notify/endpoints")
async def list_endpoints(workspace: Optional[str] = None):
    ws = _workspace(workspace)
    items = [notify_store.masked_endpoint(e) for e in notify_store.list_endpoints(ws)]
    return {"endpoints": items, "workspace": ws, "kinds": list(notify_store.ENDPOINT_KINDS)}


@router.post("/api/notify/endpoints", status_code=201)
async def create_endpoint(body: CreateEndpoint, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    try:
        record = notify_store.create_endpoint(ws, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"endpoint": notify_store.masked_endpoint(record)}


@router.patch("/api/notify/endpoints/{endpoint_id}")
async def update_endpoint(endpoint_id: str, body: UpdateEndpoint, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    record = notify_store.update_endpoint(ws, endpoint_id, changes)
    if record is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return {"endpoint": notify_store.masked_endpoint(record)}


@router.delete("/api/notify/endpoints/{endpoint_id}")
async def delete_endpoint(endpoint_id: str, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    if not notify_store.delete_endpoint(ws, endpoint_id):
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return {"deleted": True, "id": endpoint_id}


@router.post("/api/notify/endpoints/{endpoint_id}/test")
async def test_endpoint(endpoint_id: str, workspace: Optional[str] = None):
    """Send one synthetic event through this endpoint right now."""
    from uuid import uuid4

    from notify import outbound as notify_outbound

    ws = _workspace(workspace)
    record = notify_store.get_endpoint(ws, endpoint_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    event = {
        "id": str(uuid4()),
        "type": "notify.test",
        "workspace": ws,
        "created_at": _utc_now_iso(),
        "data": {
            "title": "Test notification",
            "body": f"This is a test event from the Webhooks tab for endpoint '{record.get('id')}'.",
            "severity": "info",
        },
    }
    # Delivered inline (not through the queue) so the button can report the
    # outcome instead of firing into the background and hoping.
    notify_outbound.deliver(record, event)
    return {"sent": True, "endpoint_id": endpoint_id}


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
    record = notify_store.update_rule(ws, rule_id, changes)
    if record is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"rule": record}


@router.delete("/api/notify/rules/{rule_id}")
async def delete_rule(rule_id: str, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    if not notify_store.delete_rule(ws, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True, "id": rule_id}


# ── inbound secret, for the task webhook below ───────────────────────────────

class SetInboundSecret(BaseModel):
    secret: Optional[str] = None


@router.get("/api/notify/inbound-secret")
async def get_inbound_secret(workspace: Optional[str] = None):
    ws = _workspace(workspace)
    return {"workspace": ws, "configured": bool(notify_store.get_inbound_secret(ws))}


@router.post("/api/notify/inbound-secret")
async def set_inbound_secret(body: SetInboundSecret, workspace: Optional[str] = None):
    ws = _workspace(workspace)
    notify_store.set_inbound_secret(ws, body.secret)
    return {"workspace": ws, "configured": bool(body.secret)}


# ── inbound: file a task from an external system ─────────────────────────────

class TaskWebhookRequest(BaseModel):
    title: str
    description: str = ""
    workspace: str
    priority: Optional[str] = None
    due_at: Optional[datetime] = None
    agent_id: Optional[str] = None


@router.post("/api/webhooks/tasks", status_code=201)
async def webhook_create_task(body: TaskWebhookRequest, request: Request):
    """Create a task from an external system's signed request.

    Fail-closed: a workspace with no inbound secret configured (see
    POST /api/notify/inbound-secret) cannot be posted to at all, unlike the
    node/flow triggers where an unconfigured secret keeps today's open
    behaviour. This endpoint only ever creates work, so there is no equivalent
    "already trusted by a token" fallback.
    """
    ws = body.workspace
    secret = notify_store.get_inbound_secret(ws)
    if not secret:
        raise HTTPException(status_code=401, detail="No inbound secret configured for this workspace")

    raw_body = await request.body()
    _require_signed(secret, raw_body, request, delivery_required=True)

    from tasks import CreatedBy, TaskStatus
    from tasks import service as tasks_service
    from tasks.models import coerce_priority
    from tasks.serialize import task_to_dict

    task = tasks_service.create_task(
        title=body.title,
        description=body.description or "",
        workspace=ws,
        status=TaskStatus.ready,
        created_by=CreatedBy.external,
        due_at=body.due_at,
    )
    if body.priority:
        tasks_service.update_task(task.id, priority=coerce_priority(body.priority))
    if body.agent_id:
        tasks_service.assign_agent(task.id, agent_type=body.agent_id)

    task = tasks_service.get_task(task.id) or task
    return task_to_dict(task)
