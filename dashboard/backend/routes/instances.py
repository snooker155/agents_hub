"""
/api/instances — the live agent copies.

A run is what an agent *did*; an instance is the copy that did it. This is the
route the Instances page and the per-agent Instances tab read, the one that
delivers a message to a copy, and the one that starts, stops, restarts and
publishes a resident copy (the agent page's Run, instances/carrier.py): a
process or container of its own that answers its mailbox until stopped.
A resident copy talks through its mailbox only, so the backend never runs its
agent; any other copy that has finished comes back with its history rebuilt
from its journal.

Every listing is filtered, ordered and paginated in SQL: a workspace running a
thousand copies must not cost a full table scan per refresh.
"""
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, identity
from instances import carrier, delivery, inbox, replies, store
from instances import history as instance_history
from instances import registry as instance_registry
from managers import run_manager
from tasks import service as tasks_service

router = APIRouter(prefix="/api/instances", tags=["instances"])

LOG_TAIL_BYTES = 200_000


class InstanceAttachment(BaseModel):
    """What the composer attaches to a message: the fields of
    ``chat.models.ChatAttachment``, declared here so this module does not
    import the chat package (and every pipeline behind it) to describe them."""
    filename: str = ""
    content: str = ""
    content_b64: Optional[str] = None
    mime_type: Optional[str] = None
    store_to_workspace: bool = False
    file_id: Optional[str] = None


class InstanceReference(BaseModel):
    """A hub record attached by pointer (``chat.models.ChatReference``)."""
    kind: str
    id: str
    label: str = ""


class InstanceMessage(BaseModel):
    message: str
    # The browser's multiplexed SSE client id, so a revived turn streams over the
    # connection the page already holds instead of opening another one.
    client_id: Optional[str] = None
    # Which conversation of the instance; the main one when left out.
    conversation_id: Optional[str] = None
    # A runner (a replica bound to no agent) answers for the agent named here.
    agent_id: Optional[str] = None
    # What the chat composer attaches to a turn (chat/models.py): files from
    # the computer or the workspace, and hub records by pointer. Folded into
    # the message text here (see ``_compose_message``), so every way a copy
    # takes a message — a revival, its mailbox, a runner — carries them.
    attachments: List[InstanceAttachment] = []
    references: List[InstanceReference] = []


def _compose_message(instance: Dict[str, Any], body: InstanceMessage, text: str) -> str:
    """The message with its attachments and references rendered after it.

    The chat pipelines render the two blocks under the message when they build
    the prompt (chat/context.py). A copy's mailbox and its carrier only carry
    text, so the same blocks are rendered here, once, and travel as part of
    the message: the agent reads exactly what the chat would have shown it.
    Raises the attachment's own HTTPException on a bad or oversized file.
    """
    if not body.attachments and not body.references:
        return text
    from chat.attachments import materialize_attachments
    from chat.context import context_block_lines
    from chat.models import ChatAttachment, ChatReference, ChatRequest
    from chat.references import resolve_references

    request = ChatRequest(
        agent_id=instance.get("agent_id") or (body.agent_id or "").strip() or None,
        message=text,
        workspace=instance.get("workspace"),
        project_id=instance.get("project_id"),
        conversation_id=str(instance.get("task_id") or instance.get("instance_id")),
        attachments=[ChatAttachment(**a.model_dump()) for a in body.attachments],
        references=[ChatReference(**r.model_dump()) for r in body.references],
        source="instance",
        instance_id=str(instance.get("instance_id") or ""),
    )
    materialize_attachments(request)
    resolve_references(request)
    blocks = context_block_lines(request)
    return "\n".join([text, *blocks]) if blocks else text


class InstanceLabel(BaseModel):
    label: str


class InstanceStart(BaseModel):
    agent_id: str
    workspace: Optional[str] = None
    label: Optional[str] = None
    # The environment the process runs in (docs/environments.md); None = the
    # workspace's default environment, if it has one.
    environment_id: Optional[str] = None
    # Inputs: also take the agent's tasks in the workspace; publish a public
    # address through the hub; how many runs at once (one per conversation).
    take_tasks: bool = False
    publish: bool = False
    concurrency: Optional[int] = None
    # Open the agent's own HTTP port next to the hub's address; None = the
    # agent's own setting.
    direct_port: Optional[bool] = None


class InstanceInputs(BaseModel):
    take_tasks: Optional[bool] = None
    concurrency: Optional[int] = None


class InboundSecret(BaseModel):
    secret: str


class CarrierEvents(BaseModel):
    """One event a carrier posts for fan-out (chat/turns.py EventForwarder)."""
    channels: List[str]
    event: Dict[str, Any]


def _external_url(request: Optional[Request], instance: dict) -> Optional[str]:
    token = instance.get("expose_token")
    if not token or not instance.get("is_exposed") or request is None:
        return None
    return f"{str(request.base_url).rstrip('/')}/api/external/{token}/messages"


def _service_names(instances: list) -> Dict[str, str]:
    """``{service_id: name}`` for the replicas in a page, one lookup each."""
    from services import store as service_store
    out: Dict[str, str] = {}
    for inst in instances:
        sid = inst.get("service_id")
        if sid and sid not in out:
            svc = service_store.get(str(sid))
            out[str(sid)] = str(svc.get("name") or sid) if svc else str(sid)
    return out


def _enrich(instance: dict, tasks_by_id: dict | None = None,
            request: Optional[Request] = None,
            service_names: Optional[Dict[str, str]] = None) -> dict:
    task_id = instance.get("task_id")
    task = (tasks_by_id or {}).get(str(task_id)) if task_id else None
    resident = carrier.is_resident(instance)
    service_id = instance.get("service_id")
    if service_id and service_names is None:
        service_names = _service_names([instance])
    return {
        **carrier.public_view(instance),
        "resident": resident,
        "runner": instance.get("kind") == store.RUNNER_KIND,
        "service_name": (service_names or {}).get(str(service_id)) if service_id else None,
        "task_title": task.title if task else None,
        "channel": delivery.channel_for(str(instance.get("instance_id"))),
        # A runner accepts a message too, for the agent the message names.
        "accepts_message": True,
        "delivery": "direct" if delivery.can_deliver_directly(instance) else "queued",
        "external_url": _external_url(request, instance) if resident else None,
    }


def _get_or_404(instance_id: str) -> dict:
    instance = store.get(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    return carrier.sync(instance) or instance


def _resident_or_400(instance_id: str) -> dict:
    instance = _get_or_404(instance_id)
    if not carrier.is_resident(instance):
        raise HTTPException(status_code=400,
                            detail="Only an instance started with Run has a process of its own")
    return instance


@router.get("")
async def list_instances(
    request: Request,
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    kind: Optional[str] = None,
    state: Optional[str] = None,
    live: Optional[bool] = None,
    node_id: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
    service_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """A page of instances plus the per-state counts for the header strip.

    A request naming no workspace is otherwise open to any signed-in account
    (common/auth.py authorize()); the page is additionally narrowed here to
    instances whose own workspace the caller can see, a no-op outside
    ``multi`` mode. Filtered after the SQL page is fetched, so ``total`` still
    counts the unfiltered page (common/access.py). The header counts are not
    narrowed the same way: ``counts_by_state`` aggregates in SQL and does not
    take a set of workspaces, so under ``multi`` mode without a single
    ``workspace`` given they may count instances outside what the items list
    shows. Left as-is: recomputing the tally in Python for every restricted
    caller would be the full-table scan this route exists to avoid, and the
    header strip is not itself the leak (no record is returned, only a count).
    """
    page = store.list_instances(
        limit=max(1, min(int(limit), 500)),
        offset=max(0, int(offset)),
        workspace=workspace,
        agent_id=agent_id,
        kind=kind,
        state=state,
        live=live,
        node_id=node_id,
        q=q,
        include_archived=include_archived,
        service_id=service_id,
    )
    principal = identity.request_principal(request)
    items = access.filter_by_workspace(principal, page["items"])
    items = [carrier.sync(i) or i for i in items]
    task_ids = {str(i.get("task_id")) for i in items if i.get("task_id")}
    tasks_by_id = tasks_service.get_tasks(task_ids) if task_ids else {}
    names = _service_names(items)
    return {
        **page,
        "items": [_enrich(i, tasks_by_id, request, names) for i in items],
        "counts": store.counts_by_state(workspace=workspace, agent_id=agent_id, service_id=service_id),
    }


@router.post("", status_code=201)
async def start_instance(body: InstanceStart, request: Request):
    """Start the agent in a service: the agent page's and the agent card's Run.

    Every copy lives in a service (docs/services.md). The agent's own service
    is used when it has one, else the workspace's runner, created on first
    use; the copy returned is a free replica of that service, or a new one
    when the service may still grow. The service's own limits apply, and
    nothing else: a paused service or one at its maximum is a 409.

    A start that asks for more than a place to talk to — taking the agent's
    tasks, a public address, a port of its own — needs a copy bound to the
    agent, which a runner replica is not. An agent without a service of its
    own gets one created here with those settings (what Deploy does), and
    its first replica is the copy returned.

    Returns the replica while it boots (``starting``); the page opens it right
    away. A runner replica carries ``for_agent`` so the page knows whom the
    messages typed there are for.
    """
    import asyncio

    from agents import registry as agent_registry
    from routes.services import _environment
    from services import replicas as service_replicas
    from services import routing as service_routing
    from services import store as service_store
    from services.replicas import ServiceUnavailable

    agent_id = (body.agent_id or "").strip()
    spec = agent_registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    workspace = (body.workspace or "").strip() or None
    env_id, env_name = _environment(workspace, (body.environment_id or "").strip() or None)
    if not env_id:
        env_id, env_name = service_routing.default_environment(workspace)

    needs_own = bool(body.take_tasks or body.publish or body.direct_port)
    service = service_store.find_agent_service(workspace, agent_id, env_id)
    if service is None and needs_own:
        service = service_store.create(
            name=(body.label or "").strip() or getattr(spec, "name", None) or agent_id,
            agent_id=agent_id, workspace=workspace,
            environment_id=env_id, environment_name=env_name,
            replicas_min=1, replicas_max=1,
            concurrency=int(body.concurrency) if body.concurrency else 4,
            take_tasks=bool(body.take_tasks),
            created_by=identity.current_user_id(),
        )
        if body.publish:
            service = service_store.publish(service["service_id"]) or service
    if service is None:
        service = service_store.ensure_runner(workspace, env_id, env_name)

    try:
        replica = await asyncio.to_thread(service_replicas.pick, service, allow_start=True)
    except ServiceUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = _enrich(replica, request=request)
    out["for_agent"] = agent_id
    out["service"] = {"service_id": service.get("service_id"), "name": service.get("name"),
                      "kind": service.get("kind")}
    return out


@router.get("/summary")
async def instances_summary(workspace: Optional[str] = None):
    """``{agent_id: {live, total}}`` — the running-copies badge on the agent list."""
    return {"by_agent": store.counts_by_agent(workspace=workspace),
            "counts": store.counts_by_state(workspace=workspace)}


@router.get("/{instance_id}")
async def get_instance(instance_id: str, request: Request):
    instance = _get_or_404(instance_id)
    task_id = instance.get("task_id")
    tasks_by_id = tasks_service.get_tasks([task_id]) if task_id else {}
    enriched = _enrich(instance, tasks_by_id, request)
    enriched["pending_messages"] = len(inbox.pending(instance_id))
    return enriched


@router.get("/{instance_id}/runs")
async def get_instance_runs(instance_id: str, limit: int = 50, offset: int = 0,
                            ascending: bool = False):
    """The instance's journal — the runs it performed, newest first by default."""
    _get_or_404(instance_id)
    return store.runs_for(instance_id, limit=max(1, min(int(limit), 500)),
                          offset=max(0, int(offset)), ascending=ascending)


@router.get("/{instance_id}/timeline")
async def get_instance_timeline(instance_id: str, max_turns: int = 40,
                                conversation_id: Optional[str] = None):
    """One conversation as an exchange: what was asked, what it answered, what
    it used to get there, plus anything still queued in it. The main
    conversation when ``conversation_id`` is left out."""
    instance = _get_or_404(instance_id)
    context = instance_history.describe_context(
        instance_id, max_turns=max_turns, conversation_id=conversation_id,
        service_id=instance.get("service_id") or None)
    return {**context, "pending": inbox.pending(instance_id, conversation_id,
                                                any_conversation=False)}


@router.get("/{instance_id}/context")
async def get_instance_context(instance_id: str, max_turns: int = 20,
                               conversation_id: Optional[str] = None):
    """Exactly what the next message of a conversation will carry into the prompt."""
    instance = _get_or_404(instance_id)
    return instance_history.describe_context(
        instance_id, max_turns=max_turns, conversation_id=conversation_id,
        service_id=instance.get("service_id") or None)


@router.get("/{instance_id}/conversations")
async def get_instance_conversations(instance_id: str, limit: int = 100):
    """The instance's conversations: the main one first, then the rest by
    their latest activity."""
    _get_or_404(instance_id)
    return {"items": inbox.conversations(instance_id, limit=max(1, min(int(limit), 500)))}


@router.get("/{instance_id}/messages/{msg_id}")
async def get_instance_message(instance_id: str, msg_id: str):
    """Where one message stands: queued, running, or its answer."""
    _get_or_404(instance_id)
    reply = replies.reply_for(msg_id)
    if reply is None or reply.get("instance_id") != instance_id:
        raise HTTPException(status_code=404, detail="Message not found")
    return reply


@router.get("/{instance_id}/inbox")
async def get_instance_inbox(instance_id: str, limit: int = 50):
    _get_or_404(instance_id)
    return {"items": inbox.history(instance_id, limit=limit)}


@router.get("/{instance_id}/logs")
async def get_instance_logs(instance_id: str):
    """The carrier's own log for a resident instance, the latest run's log
    otherwise. Falls back to the blob store for a carrier on another host."""
    instance = _get_or_404(instance_id)

    log_file = instance.get("carrier_log_file")
    if log_file and not Path(log_file).exists():
        try:
            from common import blobs
            text = blobs.read_text(blobs.rel(log_file))
        except Exception:
            text = None
        if text is not None:
            return {"logs": text[-LOG_TAIL_BYTES:], "log_file": log_file,
                    "truncated": len(text) > LOG_TAIL_BYTES}
    if not log_file:
        journal = store.runs_for(instance_id, limit=1)
        items = journal.get("items") or []
        log_file = items[0].get("log_file") if items else None

    if not log_file or not Path(log_file).exists():
        return {"logs": "", "log_file": log_file}
    try:
        path = Path(log_file)
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
            content = fh.read()
        return {"logs": content, "log_file": log_file, "truncated": size > LOG_TAIL_BYTES}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{instance_id}/events")
async def carrier_events(instance_id: str, body: CarrierEvents):
    """Fan one event a carrier posts out on the channels it names.

    A service replica executing a chat turn (chat/turns.py) has no broker of
    its own: it posts every event here, and this replica of the backend
    publishes it to the conversation's channel (what the Chat page, a mirror
    tab and the routing relay read) and to the instance's. Events on an
    instance channel are also kept as the run's live tail (common/live_runs),
    as they are for any carrier run.
    """
    from common import live_runs
    from common.session_broker import broker

    event = dict(body.event or {})
    if not event.get("type"):
        raise HTTPException(status_code=400, detail="event needs a type")
    published = 0
    for channel in body.channels[:8]:
        channel = str(channel or "").strip()
        if not channel:
            continue
        if channel.startswith("instance:"):
            live_runs.record_run_event(event, session_id=channel)
        await broker.apublish(channel, event)
        published += 1
    return {"ok": True, "published": published}


@router.post("/{instance_id}/message")
async def message_instance(instance_id: str, body: InstanceMessage, request: Request):
    """Write to an instance — running, idle, or long finished.

    A copy with a loop of its own is handed the message through its mailbox; any
    other copy is revived here and answers with its previous work rebuilt into
    its context.
    """
    instance = _get_or_404(instance_id)
    text = (body.message or "").strip()
    if not text and not body.attachments and not body.references:
        raise HTTPException(status_code=400, detail="Message is empty")
    text = _compose_message(instance, body, text)
    if not instance.get("agent_id"):
        # A runner: the message becomes a chat turn of the agent it names,
        # with the conversation's earlier turns (services/routing.py).
        from services import routing as service_routing
        from services import store as service_store
        try:
            service_routing.agent_for_runner(body.agent_id, instance.get("workspace"))
        except ValueError as exc:
            raise HTTPException(status_code=400 if "name the agent" in str(exc) else 404,
                                detail=str(exc))
        service = service_store.get(instance.get("service_id")) if instance.get("service_id") else None
        _replica, msg_id = await asyncio.to_thread(
            service_routing.dispatch_message, service, text, conversation_id=body.conversation_id,
            origin="web", replica=instance, agent_id=body.agent_id)
        started = await asyncio.to_thread(delivery.wake_resident, instance)
        return {"mode": "queued", "instance_id": instance_id, "msg_id": msg_id,
                "channel": delivery.channel_for(instance_id), "started": started,
                "agent_id": (body.agent_id or "").strip(),
                "conversation_id": inbox.public_conversation(
                    inbox.normalize_conversation(body.conversation_id))}

    if carrier.is_resident(instance):
        return await delivery.deliver(instance, text, client_id=body.client_id,
                                      conversation_id=body.conversation_id)

    msg_id = inbox.enqueue(instance_id, text, origin="web")
    if delivery.can_deliver_directly(instance):
        claimed = inbox.claim_next(instance_id)
        msg_id = str((claimed or {}).get("msg_id") or msg_id)
        return await delivery.deliver(instance, text, client_id=body.client_id,
                                      msg_id=msg_id)
    # A copy busy with a task run reads the message before its next model
    # call (common/steering.py); it stays in the mailbox in case the run ends
    # first, and is answered once either way.
    steered = delivery.steer_running_task(instance, text, msg_id)
    if steered:
        return {"mode": "steered", "instance_id": instance_id, "msg_id": msg_id,
                "run_id": steered, "channel": delivery.channel_for(instance_id)}
    return {"mode": "queued", "instance_id": instance_id, "msg_id": msg_id,
            "channel": delivery.channel_for(instance_id)}


@router.post("/{instance_id}/stop")
async def stop_instance(instance_id: str):
    """Stop the instance.

    A resident instance's process is stopped (it keeps its conversations and
    can be started again); any other copy has its current run stopped.
    """
    import asyncio

    instance = _get_or_404(instance_id)
    if carrier.is_resident(instance):
        stopped = await asyncio.to_thread(carrier.stop, instance_id)
        return {"ok": stopped, "stopped_run": False, "instance": store.get(instance_id)}
    stopped_run = False
    run_id = instance.get("current_run_id")
    if run_id:
        try:
            stopped_run = run_manager.stop_run_by_id(str(run_id))
        except Exception:
            stopped_run = False
    instance_registry.mark_stopped(instance_id, "stopped by operator")
    return {"ok": True, "stopped_run": stopped_run, "instance": store.get(instance_id)}


@router.post("/{instance_id}/interrupt")
async def interrupt_instance(instance_id: str):
    """Stop the runs a resident instance is working on; the process stays up
    and goes on with its mailbox."""
    _resident_or_400(instance_id)
    stopped = []
    for run in run_manager.get_in_progress_runs_for_instance(instance_id):
        run_id = str(run.get("run_id") or "")
        try:
            if run_id and run_manager.stop_run_by_id(run_id):
                stopped.append(run_id)
        except Exception:
            continue
    return {"ok": True, "stopped_runs": stopped, "instance": store.get(instance_id)}


@router.post("/{instance_id}/restart")
async def restart_instance(instance_id: str, request: Request):
    """Replace the instance's process with a fresh one (or start a stopped
    instance again). Conversations and runs stay."""
    import asyncio

    _resident_or_400(instance_id)
    try:
        instance = await asyncio.to_thread(carrier.restart, instance_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if instance is None:
        raise HTTPException(status_code=409,
                            detail="The process runs on another host; restart it from there")
    return _enrich(instance, request=request)


@router.patch("/{instance_id}/inputs")
async def update_instance_inputs(instance_id: str, body: InstanceInputs, request: Request):
    """Switch what the running instance listens to (take tasks, how many runs
    at once). Applies without a restart."""
    _resident_or_400(instance_id)
    instance = carrier.set_inputs(instance_id, take_tasks=body.take_tasks,
                                  concurrency=body.concurrency)
    return _enrich(instance or store.get(instance_id), request=request)


@router.get("/{instance_id}/carriers")
async def get_instance_carriers(instance_id: str, limit: int = 50):
    """Every process the instance has had, newest first."""
    _get_or_404(instance_id)
    return {"items": carrier.carriers(instance_id, limit=max(1, min(int(limit), 500)))}


@router.post("/{instance_id}/publish")
async def publish_instance(instance_id: str, request: Request):
    """Give the instance a public address through the hub (a fresh token;
    publishing again rotates it)."""
    _resident_or_400(instance_id)
    updated = carrier.publish_instance(instance_id)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to publish the instance")
    return _enrich(updated, request=request)


@router.delete("/{instance_id}/publish")
async def unpublish_instance(instance_id: str, request: Request):
    """Withdraw the public address; the token stops working at once."""
    _resident_or_400(instance_id)
    carrier.unpublish_instance(instance_id)
    return _enrich(store.get(instance_id) or {}, request=request)


@router.put("/{instance_id}/inbound-secret")
async def set_instance_inbound_secret(instance_id: str, data: InboundSecret, request: Request):
    """Require a signature on the instance's public calls.

    Once set, the public address refuses any request not signed with it (see
    routes/external.py and docs/notifications.md). Write-only: it is never
    returned, the record only says whether one is set.
    """
    _resident_or_400(instance_id)
    secret = (data.secret or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="secret must not be empty")
    updated = carrier.set_inbound_secret(instance_id, secret)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to store the inbound secret")
    return _enrich(updated, request=request)


@router.delete("/{instance_id}/inbound-secret")
async def clear_instance_inbound_secret(instance_id: str, request: Request):
    """Drop the inbound secret: public calls go back to token only."""
    _resident_or_400(instance_id)
    updated = carrier.set_inbound_secret(instance_id, None)
    return _enrich(updated or store.get(instance_id) or {}, request=request)


@router.get("/{instance_id}/connections")
async def get_instance_connections(instance_id: str):
    """The public calls this instance received, newest first."""
    _get_or_404(instance_id)
    return carrier.get_connections(instance_id)


@router.patch("/{instance_id}")
async def rename_instance(instance_id: str, body: InstanceLabel):
    """Rename a copy — with a thousand of them, the label is the only handle."""
    _get_or_404(instance_id)
    updated = store.update(instance_id, label=(body.label or "").strip()[:200])
    return updated


@router.delete("/{instance_id}")
async def delete_instance(instance_id: str):
    """Forget the instance. Its runs survive as Messages — they are the record."""
    instance = _get_or_404(instance_id)
    if carrier.is_resident(instance):
        if instance.get("state") in store.LIVE_STATES:
            raise HTTPException(status_code=400, detail="Stop the instance before deleting it")
        return {"ok": carrier.remove(instance_id)}
    if instance.get("state") == "active":
        raise HTTPException(status_code=400,
                            detail="Stop the instance before deleting it")
    removed = store.delete(instance_id)
    return {"ok": removed}
