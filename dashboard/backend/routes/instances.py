"""
/api/instances — the live agent copies.

A run is what an agent *did*; an instance is the copy that did it. This is the
route the Instances page and the per-agent Instances tab read, and the one that
delivers a message to a copy — including one that has already finished, which
comes back with its own history rebuilt from its journal.

Every listing is filtered, ordered and paginated in SQL: a workspace running a
thousand copies must not cost a full table scan per refresh.
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, identity
from instances import delivery, inbox, store
from instances import history as instance_history
from instances import registry as instance_registry
from managers import run_manager
from tasks import service as tasks_service

router = APIRouter(prefix="/api/instances", tags=["instances"])

LOG_TAIL_BYTES = 200_000


class InstanceMessage(BaseModel):
    message: str
    # The browser's multiplexed SSE client id, so a revived turn streams over the
    # connection the page already holds instead of opening another one.
    client_id: Optional[str] = None


class InstanceLabel(BaseModel):
    label: str


def _enrich(instance: dict, tasks_by_id: dict | None = None) -> dict:
    task_id = instance.get("task_id")
    task = (tasks_by_id or {}).get(str(task_id)) if task_id else None
    return {
        **instance,
        "task_title": task.title if task else None,
        "channel": delivery.channel_for(str(instance.get("instance_id"))),
        "accepts_message": True,
        "delivery": "direct" if delivery.can_deliver_directly(instance) else "queued",
    }


def _get_or_404(instance_id: str) -> dict:
    instance = store.get(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
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
    )
    principal = identity.request_principal(request)
    items = access.filter_by_workspace(principal, page["items"])
    task_ids = {str(i.get("task_id")) for i in items if i.get("task_id")}
    tasks_by_id = tasks_service.get_tasks(task_ids) if task_ids else {}
    return {
        **page,
        "items": [_enrich(i, tasks_by_id) for i in items],
        "counts": store.counts_by_state(workspace=workspace, agent_id=agent_id),
    }


@router.get("/summary")
async def instances_summary(workspace: Optional[str] = None):
    """``{agent_id: {live, total}}`` — the running-copies badge on the agent list."""
    return {"by_agent": store.counts_by_agent(workspace=workspace),
            "counts": store.counts_by_state(workspace=workspace)}


@router.get("/{instance_id}")
async def get_instance(instance_id: str):
    instance = _get_or_404(instance_id)
    task_id = instance.get("task_id")
    tasks_by_id = tasks_service.get_tasks([task_id]) if task_id else {}
    enriched = _enrich(instance, tasks_by_id)
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
async def get_instance_timeline(instance_id: str, max_turns: int = 40):
    """The exchange as a conversation: what was asked, what it answered, what it
    used to get there — plus anything still queued for it."""
    _get_or_404(instance_id)
    context = instance_history.describe_context(instance_id, max_turns=max_turns)
    return {**context, "pending": inbox.pending(instance_id)}


@router.get("/{instance_id}/context")
async def get_instance_context(instance_id: str, max_turns: int = 20):
    """Exactly what the next message will carry into the prompt."""
    _get_or_404(instance_id)
    return instance_history.describe_context(instance_id, max_turns=max_turns)


@router.get("/{instance_id}/inbox")
async def get_instance_inbox(instance_id: str, limit: int = 50):
    _get_or_404(instance_id)
    return {"items": inbox.history(instance_id, limit=limit)}


@router.get("/{instance_id}/logs")
async def get_instance_logs(instance_id: str):
    """The carrier's log for a node, the latest run's log otherwise."""
    instance = _get_or_404(instance_id)

    log_file = None
    if instance.get("node_id"):
        try:
            from managers import node_manager
            node = node_manager.get_node(str(instance["node_id"]))
            log_file = (node or {}).get("log_file")
        except Exception:
            log_file = None
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


@router.post("/{instance_id}/message")
async def message_instance(instance_id: str, body: InstanceMessage, request: Request):
    """Write to an instance — running, idle, or long finished.

    A copy with a loop of its own is handed the message through its mailbox; any
    other copy is revived here and answers with its previous work rebuilt into
    its context.
    """
    instance = _get_or_404(instance_id)
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message is empty")
    if not instance.get("agent_id"):
        raise HTTPException(status_code=400, detail="Instance has no agent to run")

    msg_id = inbox.enqueue(instance_id, text, origin="web")
    if delivery.can_deliver_directly(instance):
        claimed = inbox.claim_next(instance_id)
        msg_id = str((claimed or {}).get("msg_id") or msg_id)
        return await delivery.deliver(instance, text, client_id=body.client_id,
                                      msg_id=msg_id)
    return {"mode": "queued", "instance_id": instance_id, "msg_id": msg_id,
            "channel": delivery.channel_for(instance_id)}


@router.post("/{instance_id}/stop")
async def stop_instance(instance_id: str):
    """Stop whatever the instance is doing; the carrier keeps its own controls."""
    instance = _get_or_404(instance_id)
    stopped_run = False
    run_id = instance.get("current_run_id")
    if run_id:
        try:
            stopped_run = run_manager.stop_run_by_id(str(run_id))
        except Exception:
            stopped_run = False
    if instance.get("kind") in delivery.CARRIER_KINDS:
        # A node stays up — stopping its work is not stopping the node, which is
        # done from the node's own page.
        instance_registry.mark_standby(instance_id, "stopped by operator")
    else:
        instance_registry.mark_stopped(instance_id, "stopped by operator")
    return {"ok": True, "stopped_run": stopped_run, "instance": store.get(instance_id)}


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
    if instance.get("state") == "active":
        raise HTTPException(status_code=400,
                            detail="Stop the instance before deleting it")
    removed = store.delete(instance_id)
    return {"ok": removed}
