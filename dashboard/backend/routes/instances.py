"""
/api/instances — the live agent copies.

A run is what an agent *did*; an instance is the copy that did it. This is the
route the Instances page and the per-agent Instances tab read, the one that
delivers a message to a copy, and the one that starts, stops and restarts
a resident copy (the agent page's Run, instances/carrier.py): a
process or container of its own that answers its mailbox until stopped.
A resident copy talks through its mailbox only, so the backend never runs its
agent; any other copy that has finished comes back with its history rebuilt
from its journal.

Every listing is filtered, ordered and paginated in SQL: a workspace running a
thousand copies must not cost a full table scan per refresh.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import identity
from instances import carrier, delivery, inbox, replies, store
from instances import history as instance_history
from instances import registry as instance_registry
from managers import run_manager
from routes.messages import build_run_trail
from tasks import service as tasks_service

log = logging.getLogger(__name__)

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
    # What the chat composer attaches to a turn (chat/models.py): files from
    # the computer or the workspace, and hub records by pointer. Folded into
    # the message text here (see ``_compose_message``), so both ways a copy
    # takes a message, a revival and its mailbox, carry them.
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
        agent_id=instance.get("agent_id") or None,
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
    # Inputs: also take the agent's tasks in the workspace; how many runs at
    # once (one per conversation).
    take_tasks: bool = False
    concurrency: Optional[int] = None


class InstanceInputs(BaseModel):
    take_tasks: Optional[bool] = None
    concurrency: Optional[int] = None


class CarrierEvents(BaseModel):
    """One event a carrier posts for fan-out (chat/turns.py EventForwarder)."""
    channels: List[str]
    event: Dict[str, Any]


def _enrich(instance: dict, tasks_by_id: dict | None = None,
            request: Optional[Request] = None) -> dict:
    task_id = instance.get("task_id")
    task = (tasks_by_id or {}).get(str(task_id)) if task_id else None
    resident = carrier.is_resident(instance)
    return {
        **carrier.public_view(instance),
        "resident": resident,
        "task_title": task.title if task else None,
        "channel": delivery.channel_for(str(instance.get("instance_id"))),
        "accepts_message": bool(instance.get("agent_id")),
        "delivery": "direct" if delivery.can_deliver_directly(instance) else "queued",
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
    limit: int = 100,
    offset: int = 0,
    sort: Optional[str] = None,
    order: Optional[str] = None,
):
    """A page of instances plus the per-state counts for the header strip.

    ``sort`` is ``live`` (the default: live copies first, newest activity
    first) or a key of ``store.INSTANCE_SORTS``; ``order`` is ``asc``/``desc``;
    the list pages load rows in batches and refetch the whole loaded window
    from the top, so ``limit`` goes up to 2000.
    """
    try:
        page = store.list_instances(
            limit=max(1, min(int(limit), 2000)),
            offset=max(0, int(offset)),
            sort=sort,
            order=order,
            workspace=workspace,
            agent_id=agent_id,
            kind=kind,
            state=state,
            live=live,
            node_id=node_id,
            q=q,
            include_archived=include_archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    items = [carrier.sync(i) or i for i in page["items"]]
    task_ids = {str(i.get("task_id")) for i in items if i.get("task_id")}
    tasks_by_id = tasks_service.get_tasks(task_ids) if task_ids else {}
    return {
        **page,
        "items": [_enrich(i, tasks_by_id, request) for i in items],
        "counts": store.counts_by_state(workspace=workspace, agent_id=agent_id),
    }


@router.post("", status_code=201)
async def start_instance(body: InstanceStart, request: Request):
    """Start a resident copy of the agent: the agent page's and the agent
    card's Run. The copy is returned while it boots (``starting``), so the
    page can open it right away. The workspace's per-agent capacity applies:
    one copy too many is a 409.
    """
    from agents import registry as agent_registry

    agent_id = (body.agent_id or "").strip()
    if not agent_registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    workspace = (body.workspace or "").strip() or None
    try:
        instance = await asyncio.to_thread(
            carrier.start, agent_id,
            workspace=workspace,
            label=(body.label or "").strip() or None,
            take_tasks=bool(body.take_tasks),
            concurrency=int(body.concurrency) if body.concurrency else None,
            started_by=identity.current_user_id(),
        )
    except carrier.CapacityReached as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _enrich(instance, request=request)


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
    _get_or_404(instance_id)
    context = instance_history.describe_context(
        instance_id, max_turns=max_turns, conversation_id=conversation_id)
    for turn in context.get("turns") or []:
        trail = None
        if isinstance(turn, dict) and turn.get("run_id"):
            try:
                trail = build_run_trail(turn["run_id"])
            except Exception:  # noqa: BLE001 - a broken trail must not hide the turn
                trail = None
        turn["timeline"] = (trail or {}).get("timeline") or []
        turn["files"] = (trail or {}).get("files") or []
        turn["stats"] = (trail or {}).get("stats") or {"tools": 0, "errors": 0, "thoughts": 0}
    return {**context, "pending": inbox.pending(instance_id, conversation_id,
                                                any_conversation=False)}


@router.get("/{instance_id}/context")
async def get_instance_context(instance_id: str, max_turns: int = 20,
                               conversation_id: Optional[str] = None):
    """Exactly what the next message of a conversation will carry into the prompt."""
    _get_or_404(instance_id)
    return instance_history.describe_context(
        instance_id, max_turns=max_turns, conversation_id=conversation_id)


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
        except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
            log.debug("get_instance_logs: falling back after a failure", exc_info=True)
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
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{instance_id}/events")
async def carrier_events(instance_id: str, body: CarrierEvents):
    """Fan one event a carrier posts out on the channels it names.

    A carrier process has no broker of its own: it posts every event here,
    and the backend publishes it to the conversation's channel (what the Chat
    page and a mirror tab read) and to the instance's. A chat turn's events
    are also kept as the conversation's live turn, and any other carrier
    run's as the run's live tail (common/live_runs), so the backend can say
    what a conversation or a run is in the middle of.
    """
    from common import live_runs
    from common.session_broker import broker

    event = dict(body.event or {})
    if not event.get("type"):
        raise HTTPException(status_code=400, detail="event needs a type")
    channels = [str(c or "").strip() for c in body.channels[:8]]
    channels = [c for c in channels if c]
    # A chat turn's events are kept once, by conversation (which also knows
    # the run): the same event posted to the instance's channel as well must
    # not be folded a second time.
    for_chat = any(c.startswith("chat:") for c in channels)
    published = 0
    for channel in channels:
        if channel.startswith("chat:"):
            change = live_runs.record_conversation_event(channel[len("chat:"):], event)
            if change:
                from chat.broadcast import announce_running_change
                announce_running_change(channel[len("chat:"):])
        elif channel.startswith("instance:") and not for_chat:
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
        raise HTTPException(status_code=400, detail="This instance runs no agent of its own")

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
        except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
            log.debug("stop_instance: falling back after a failure", exc_info=True)
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
        except Exception:  # noqa: BLE001 - one run that will not stop does not keep the others running
            log.warning("could not stop run %s of instance %s", run_id, instance_id, exc_info=True)
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
        raise HTTPException(status_code=500, detail=str(e)) from e
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
    row = instance or store.get(instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return _enrich(row, request=request)


@router.get("/{instance_id}/carriers")
async def get_instance_carriers(instance_id: str, limit: int = 50):
    """Every process the instance has had, newest first."""
    _get_or_404(instance_id)
    return {"items": carrier.carriers(instance_id, limit=max(1, min(int(limit), 500)))}


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
