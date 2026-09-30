"""
/api/services: agents kept running as replicas (docs/services.md).

A service is a desired state (services/store.py); its replicas are resident
instances carrying its id, kept in line by the supervisor
(services/supervisor.py). This router creates and changes services, pauses
and resumes them, adds a replica by hand, reads the supervisor's journal,
gives a service a public address, and writes a message to it (answered by
whichever replica the routing picks, with the service conversation's history).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common import access, identity
from instances import carrier, delivery, inbox, replies
from instances import history as instance_history
from services import replicas, store
from services import routing as service_routing

router = APIRouter(prefix="/api/services", tags=["services"])


class ServiceCreate(BaseModel):
    # None: a runner, which answers any agent's chat turn.
    agent_id: Optional[str] = None
    workspace: Optional[str] = None
    environment_id: Optional[str] = None
    name: Optional[str] = None
    replicas_min: int = Field(1, ge=0, le=store.MAX_REPLICAS)
    replicas_max: int = Field(1, ge=0, le=store.MAX_REPLICAS)
    concurrency: int = Field(4, ge=1, le=store.MAX_CONCURRENCY)
    take_tasks: bool = False
    idle_stop_seconds: int = Field(600, ge=0)
    budget_usd: Optional[float] = Field(None, ge=0)
    agent_version: Optional[int] = Field(None, ge=1)
    publish: bool = False


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    replicas_min: Optional[int] = Field(None, ge=0, le=store.MAX_REPLICAS)
    replicas_max: Optional[int] = Field(None, ge=0, le=store.MAX_REPLICAS)
    concurrency: Optional[int] = Field(None, ge=1, le=store.MAX_CONCURRENCY)
    take_tasks: Optional[bool] = None
    idle_stop_seconds: Optional[int] = Field(None, ge=0)
    budget_usd: Optional[float] = Field(None, ge=0)
    clear_budget: bool = False
    agent_version: Optional[int] = Field(None, ge=1)
    clear_agent_version: bool = False
    environment_id: Optional[str] = None
    clear_environment: bool = False


class ServiceMessage(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    client_id: Optional[str] = None
    # A runner answers for the agent named here (docs/services.md).
    agent_id: Optional[str] = None


class InboundSecret(BaseModel):
    secret: str


def _external_url(request: Optional[Request], service: Dict[str, Any]) -> Optional[str]:
    token = service.get("expose_token")
    if not token or not service.get("is_exposed") or request is None:
        return None
    return f"{str(request.base_url).rstrip('/')}/api/external/{token}/messages"


def _enrich(service: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    return {
        **store.public_view(service),
        "replicas": replicas.summary(str(service["service_id"])),
        "external_url": _external_url(request, service),
    }


def _get_or_404(service_id: str) -> Dict[str, Any]:
    service = store.get(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return service


def _environment(workspace: Optional[str], environment_id: Optional[str]):
    """``(id, name)`` of the named environment, validated for the workspace."""
    if not environment_id:
        return None, None
    try:
        from environments import service as env_service
        env = env_service.resolve_for(workspace, environment_id)
    except ImportError:
        raise HTTPException(status_code=400, detail="environments are not available")
    except Exception as exc:  # noqa: BLE001 - unknown, archived, foreign: the message says which
        raise HTTPException(status_code=400, detail=str(exc))
    if env is None:
        raise HTTPException(status_code=400, detail=f"Unknown environment: {environment_id}")
    return str(env.id), str(env.name)


@router.get("")
async def list_services(request: Request, workspace: Optional[str] = None,
                        agent_id: Optional[str] = None, kind: Optional[str] = None,
                        include_runners: bool = True):
    items = store.list_services(workspace=workspace, agent_id=agent_id, kind=kind,
                                include_runners=include_runners)
    principal = identity.request_principal(request)
    items = access.filter_by_workspace(principal, items)
    return {"items": [_enrich(s, request) for s in items], "total": len(items)}


@router.get("/chat-route")
async def chat_route(workspace: Optional[str] = None, agent_id: Optional[str] = None):
    """Where a chat turn for this agent in this workspace would run, and
    whether it can: the agent's own service when it has one, else the
    workspace's runner. Read-only, for the chat page's and the Services
    page's warning: a runner that does not exist yet is created by the first
    turn (services/routing.py), so it counts as available."""
    from chat import routing as chat_routing

    if not chat_routing.enabled():
        return {"mode": "inprocess", "service": None, "available": True, "reason": None}
    env_id, _env_name = service_routing.default_environment(workspace)
    svc = store.find_agent_service(workspace, agent_id, env_id) if agent_id else None
    if svc is None:
        svc = store.find_runner(workspace, env_id)
    if svc is None:
        return {"mode": "instances", "service": None, "available": True, "reason": None}
    reason = None
    if svc.get("status") != store.STATUS_ACTIVE:
        reason = "paused"
    elif int(svc.get("replicas_max") or 0) <= 0 and not replicas.live_replicas(svc):
        reason = "no_replicas"
    return {
        "mode": "instances",
        "available": reason is None,
        "reason": reason,
        "service": {
            "service_id": svc.get("service_id"), "name": svc.get("name"), "kind": svc.get("kind"),
            "agent_id": svc.get("agent_id"), "status": svc.get("status"),
            "paused_reason": svc.get("paused_reason"),
        },
    }


@router.post("", status_code=201)
async def create_service(body: ServiceCreate, request: Request):
    """Deploy: create a service and let the supervisor bring its replicas up.

    The first replica up to the minimum is started here, so the page that
    opens next shows a process booting rather than an empty service.
    """
    from agents import registry as agent_registry

    agent_id = (body.agent_id or "").strip() or None
    if agent_id and not agent_registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    workspace = store.normalize_workspace(body.workspace)
    env_id, env_name = _environment(workspace, body.environment_id)
    if agent_id:
        # The workspace's number for the agent (its capacity override, 1 when
        # it has none, no limit in the default workspace) is how many
        # services of the agent it allows: copies are counted by the
        # services' own replica limits, not here.
        from instances.carrier import workspace_capacity
        limit = workspace_capacity(workspace, agent_id)
        own = store.list_services(workspace=workspace, agent_id=agent_id, kind=store.KIND_AGENT)
        if limit is not None and len(own) >= limit:
            raise HTTPException(
                status_code=409,
                detail=f"'{agent_id}' already has {len(own)} service(s) in workspace '{workspace}', "
                       f"the {limit} it allows; raise the limit on the agent's page, "
                       "or change or delete an existing service")
    name = (body.name or "").strip() or (f"{agent_id}" if agent_id else f"{workspace} runner")
    try:
        service = store.create(
            name=name, agent_id=agent_id, workspace=workspace, environment_id=env_id,
            environment_name=env_name, replicas_min=body.replicas_min,
            replicas_max=body.replicas_max, concurrency=body.concurrency,
            take_tasks=body.take_tasks, idle_stop_seconds=body.idle_stop_seconds,
            budget_usd=body.budget_usd, agent_version=body.agent_version,
            created_by=identity.current_user_id(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    store.add_event(service["service_id"], "created",
                    f"min {service['replicas_min']}, max {service['replicas_max']}, "
                    f"concurrency {service['concurrency']}")
    if body.publish:
        service = store.publish(service["service_id"]) or service
    if int(service.get("replicas_min") or 0) > 0:
        try:
            await asyncio.to_thread(replicas.start_replica, service, reason="created")
        except Exception as exc:  # noqa: BLE001 - journaled; the supervisor retries on its tick
            store.add_event(service["service_id"], "replica_failed", str(exc))
    return _enrich(store.get(service["service_id"]) or service, request)


@router.get("/{service_id}")
async def get_service(service_id: str, request: Request):
    return _enrich(_get_or_404(service_id), request)


@router.patch("/{service_id}")
async def update_service(service_id: str, body: ServiceUpdate, request: Request):
    """Change the desired state; the supervisor applies it on its next tick
    (a raised minimum starts replicas, a lowered maximum stops idle ones)."""
    service = _get_or_404(service_id)
    updates: Dict[str, Any] = {}
    for field in ("name", "replicas_min", "replicas_max", "concurrency", "take_tasks",
                  "idle_stop_seconds", "budget_usd", "agent_version"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value
    if body.clear_budget:
        updates["budget_usd"] = None
    if body.clear_agent_version:
        updates["agent_version"] = None
    if body.environment_id is not None:
        env_id, env_name = _environment(service.get("workspace"), body.environment_id)
        updates["environment_id"], updates["environment_name"] = env_id, env_name
    if body.clear_environment:
        updates["environment_id"] = None
        updates["environment_name"] = None
    if "name" in updates:
        updates["name"] = str(updates["name"]).strip()[:120] or service.get("name")
    updated = store.update(service_id, **updates) if updates else service
    if updates:
        store.add_event(service_id, "updated", ", ".join(f"{k}={v}" for k, v in updates.items()))
        # Running replicas read their inputs from their row: keep them in step.
        if "concurrency" in updates or "take_tasks" in updates:
            for rep in replicas.live_replicas(updated):
                carrier.set_inputs(rep["instance_id"], take_tasks=updates.get("take_tasks"),
                                   concurrency=updates.get("concurrency"))
    return _enrich(updated or service, request)


@router.post("/{service_id}/pause")
async def pause_service(service_id: str, request: Request):
    """Stop every replica and start none until resumed."""
    _get_or_404(service_id)
    service = store.pause(service_id, "paused by operator")
    for rep in replicas.live_replicas(service):
        await asyncio.to_thread(replicas.stop_replica, service, rep, reason="paused")
    return _enrich(store.get(service_id) or service, request)


@router.post("/{service_id}/resume")
async def resume_service(service_id: str, request: Request):
    _get_or_404(service_id)
    service = store.resume(service_id)
    if int(service.get("replicas_min") or 0) > 0:
        try:
            await asyncio.to_thread(replicas.start_replica, service, reason="resumed")
        except Exception as exc:  # noqa: BLE001
            store.add_event(service_id, "replica_failed", str(exc))
    return _enrich(store.get(service_id) or service, request)


@router.delete("/{service_id}")
async def delete_service(service_id: str):
    """Remove the service; its replicas are stopped and unlinked, their runs
    stay readable."""
    service = _get_or_404(service_id)
    for rep in replicas.live_replicas(service):
        await asyncio.to_thread(replicas.stop_replica, service, rep, reason="service deleted")
    return {"ok": store.delete(service_id)}


@router.get("/{service_id}/replicas")
async def list_replicas(service_id: str, live: Optional[bool] = None, request: Request = None):
    _get_or_404(service_id)
    items = replicas.list_replicas(service_id, live=live)
    return {"items": [{**carrier.public_view(i), "resident": True,
                       "channel": delivery.channel_for(str(i["instance_id"]))} for i in items],
            "total": len(items)}


@router.post("/{service_id}/replicas", status_code=201)
async def add_replica(service_id: str, request: Request):
    """Start one more replica now, up to the maximum."""
    service = _get_or_404(service_id)
    if service.get("status") != store.STATUS_ACTIVE:
        raise HTTPException(status_code=409, detail="The service is paused")
    live = replicas.live_replicas(service)
    if len(live) >= int(service.get("replicas_max") or 0):
        raise HTTPException(status_code=409,
                            detail=f"The service already runs its maximum of {service.get('replicas_max')}")
    try:
        replica = await asyncio.to_thread(replicas.start_replica, service, reason="added by operator")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {**carrier.public_view(replica), "resident": True}


@router.get("/{service_id}/events")
async def service_events(service_id: str, limit: int = 100):
    _get_or_404(service_id)
    return {"items": store.events(service_id, limit=max(1, min(int(limit), 500)))}


@router.get("/{service_id}/conversations")
async def service_conversations(service_id: str, limit: int = 100):
    _get_or_404(service_id)
    return {"items": inbox.service_conversations(service_id, limit=max(1, min(int(limit), 500)))}


@router.get("/{service_id}/timeline")
async def service_timeline(service_id: str, conversation_id: Optional[str] = None,
                           max_turns: int = 40):
    """One conversation of the service, across every replica that answered it."""
    _get_or_404(service_id)
    return instance_history.describe_context(None, max_turns=max_turns,
                                             conversation_id=conversation_id, service_id=service_id)


@router.post("/{service_id}/message")
async def message_service(service_id: str, body: ServiceMessage):
    """Write to the service: a replica answers with the conversation's history."""
    from common.session_broker import broker

    service = _get_or_404(service_id)
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message is empty")
    if not service.get("agent_id"):
        try:
            service_routing.agent_for_runner(body.agent_id, service.get("workspace"))
        except ValueError as exc:
            raise HTTPException(status_code=400 if "name the agent" in str(exc) else 404,
                                detail=str(exc))
    try:
        replica = await asyncio.to_thread(replicas.pick, service, conversation_id=body.conversation_id)
    except replicas.ServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not start a replica: {exc}")
    channel = delivery.channel_for(str(replica["instance_id"]))
    if body.client_id:
        broker.add_channel(body.client_id, channel)
    replica, msg_id = await asyncio.to_thread(
        service_routing.dispatch_message, service, text, conversation_id=body.conversation_id,
        origin="web", replica=replica, agent_id=body.agent_id)
    return {"mode": "queued", "service_id": service_id, "instance_id": replica["instance_id"],
            "msg_id": msg_id, "channel": channel,
            "conversation_id": inbox.public_conversation(inbox.normalize_conversation(body.conversation_id))}


@router.get("/{service_id}/messages/{msg_id}")
async def service_message_status(service_id: str, msg_id: str):
    _get_or_404(service_id)
    reply = replies.reply_for(msg_id)
    if reply is None or not _belongs(reply.get("instance_id"), service_id):
        raise HTTPException(status_code=404, detail="Message not found")
    return reply


def _belongs(instance_id: Optional[str], service_id: str) -> bool:
    from instances import store as istore
    inst = istore.get(str(instance_id or ""))
    return bool(inst) and str(inst.get("service_id") or "") == str(service_id)


@router.post("/{service_id}/publish")
async def publish_service(service_id: str, request: Request):
    _get_or_404(service_id)
    updated = store.publish(service_id)
    return _enrich(updated or _get_or_404(service_id), request)


@router.delete("/{service_id}/publish")
async def unpublish_service(service_id: str, request: Request):
    _get_or_404(service_id)
    updated = store.unpublish(service_id)
    return _enrich(updated or _get_or_404(service_id), request)


@router.put("/{service_id}/inbound-secret")
async def set_service_inbound_secret(service_id: str, data: InboundSecret, request: Request):
    _get_or_404(service_id)
    secret = (data.secret or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="secret must not be empty")
    updated = store.set_inbound_secret(service_id, secret)
    return _enrich(updated or _get_or_404(service_id), request)


@router.delete("/{service_id}/inbound-secret")
async def clear_service_inbound_secret(service_id: str, request: Request):
    _get_or_404(service_id)
    updated = store.set_inbound_secret(service_id, None)
    return _enrich(updated or _get_or_404(service_id), request)


@router.get("/{service_id}/connections")
async def service_connections(service_id: str):
    """The public calls this service received, newest first."""
    _get_or_404(service_id)
    return carrier.get_connections(service_id)


def services_map() -> List[Dict[str, Any]]:
    """The cluster map's services section (routes/deployment.py)."""
    out = []
    for svc in store.list_services(limit=1000):
        out.append({
            "service_id": svc["service_id"], "name": svc.get("name"), "kind": svc.get("kind"),
            "agent_id": svc.get("agent_id"), "workspace": svc.get("workspace"),
            "environment_name": svc.get("environment_name"), "status": svc.get("status"),
            "replicas_min": svc.get("replicas_min"), "replicas_max": svc.get("replicas_max"),
            "replicas": replicas.summary(svc["service_id"]),
        })
    return out
