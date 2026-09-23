"""
/api/deployment: the map of a deployment.

One answer to "where is everything running, how busy is it, and is it
alive": the members (backend replicas and workers, from ``common/members.py``),
which of them holds which singleton role (``common/leases.py``), the launch
queue and the outbox, and the working entities grouped by the host they run
on: agent runs (with the age of their heartbeat), flow runs, loops (with the
replica executing them), nodes and containers. ``/api/health`` stays the
"is this process healthy" snapshot; this is the whole deployment.

A member's own log is served here too, from its host's file or the
object-store mirror, and tailed live over the stream on
``logs:member:<member id>`` (common/live_state.py) like a node's.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/deployment", tags=["deployment"])

ACTIVE_RUN_STATUSES = ("running", "stop", "pending", "queued")


def _age(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _active_runs() -> List[Dict[str, Any]]:
    from managers.runs.store import query_runs

    out: List[Dict[str, Any]] = []
    for status in ACTIVE_RUN_STATUSES:
        try:
            page = query_runs(status=status, limit=500)
        except Exception:
            continue
        items = page.get("items") if isinstance(page, dict) else page
        for rec in items or []:
            out.append({
                "run_id": rec.get("run_id"), "agent_id": rec.get("agent_id"),
                "task_id": rec.get("task_id"), "workspace": rec.get("workspace"),
                "status": rec.get("status"), "host": rec.get("host") or "",
                "pid": rec.get("pid"), "container_name": rec.get("container_name"),
                "started_at": rec.get("started_at"),
                "heartbeat_at": rec.get("heartbeat_at"),
                "heartbeat_age_seconds": _age(rec.get("heartbeat_at")),
                "checkpoint_step": rec.get("checkpoint_step"),
                "resume_attempts": rec.get("resume_attempts"),
                "log_file": rec.get("log_file"),
            })
    return out


def _active_flow_runs() -> List[Dict[str, Any]]:
    try:
        from flow import run_store
        rows = run_store.load_flow_runs()
    except Exception:
        return []
    out = []
    for rec in rows:
        if str(rec.get("status") or "") not in ("running", "pending"):
            continue
        out.append({
            "flow_run_id": rec.get("flow_run_id"), "flow_id": rec.get("flow_id"),
            "task_id": rec.get("task_id"), "workspace": rec.get("workspace"),
            "status": rec.get("status"), "host": rec.get("host") or "",
            "pid": rec.get("pid"), "started_at": rec.get("started_at"),
            "heartbeat_at": rec.get("heartbeat_at"),
            "heartbeat_age_seconds": _age(rec.get("heartbeat_at")),
            "resume_attempts": rec.get("resume_attempts"),
        })
    return out


def _active_loops() -> List[Dict[str, Any]]:
    try:
        from loops import store as loop_store
        runs = loop_store.list_runs(limit=200)
    except Exception:
        return []
    out = []
    for run in runs:
        if run.status != "running":
            continue
        position = dict(run.position or {})
        out.append({
            "loop_run_id": run.loop_run_id, "loop_id": run.loop_id,
            "workspace": getattr(run, "workspace", None), "status": run.status,
            "owner": position.get("owner") or "", "host": position.get("host") or "",
            "iterations_done": position.get("iterations_done"),
            "heartbeat_at": position.get("heartbeat_at"),
            "heartbeat_age_seconds": _age(position.get("heartbeat_at")),
        })
    return out


def _nodes() -> List[Dict[str, Any]]:
    try:
        from managers import node_manager
        nodes = node_manager.list_nodes()
    except Exception:
        return []
    return [{
        "node_id": n.get("node_id"), "agent_id": n.get("agent_id"), "label": n.get("label"),
        "workspace": n.get("workspace"), "status": n.get("status"),
        "host": n.get("host") or "", "pid": n.get("pid"),
        "container_name": n.get("container_name"), "execution_mode": n.get("execution_mode"),
        "started_at": n.get("started_at"), "http_url": n.get("http_url"),
    } for n in nodes if str(n.get("status") or "") in ("running", "starting", "stopping")]


def _containers() -> List[Dict[str, Any]]:
    try:
        from managers import container_manager
        return container_manager.list_containers()
    except Exception:
        return []


def _by_host(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for it in items:
        counts[str(it.get("host") or "")] += 1
    return dict(counts)


def build_map() -> Dict[str, Any]:
    """The whole picture, as one JSON document. Every part is best-effort:
    a store that cannot be read is reported empty, never as a failure of
    the map itself."""
    from common import leases, members, run_queue
    from common.config import hub_role

    try:
        member_rows = members.list_members()
    except Exception:
        member_rows = []
    try:
        lease_rows = leases.all_leases()
    except Exception:
        lease_rows = []
    by_owner: Dict[str, List[str]] = defaultdict(list)
    for lease in lease_rows:
        if not lease.get("expired"):
            by_owner[str(lease.get("owner") or "")].append(str(lease.get("role")))
    for m in member_rows:
        m["leases"] = sorted(by_owner.get(str(m.get("member_id")), []))

    runs = _active_runs()
    flows = _active_flow_runs()
    loops = _active_loops()
    nodes = _nodes()
    containers = _containers()

    try:
        queue = run_queue.stats()
        queued = run_queue.list_queue(statuses=[run_queue.STATUS_QUEUED, run_queue.STATUS_LEASED,
                                                run_queue.STATUS_RUNNING], limit=100)
    except Exception:
        queue, queued = {}, []
    try:
        from notify import outbound
        outbox = outbound.stats()
    except Exception:
        outbox = {}

    hosts = sorted({str(m.get("host") or "") for m in member_rows}
                   | set(_by_host(runs)) | set(_by_host(nodes)) | set(_by_host(containers)))
    hosts = [h for h in hosts if h]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "self": {"member_id": leases.owner_id(), "role": hub_role()},
        "members": member_rows,
        "leases": lease_rows,
        "queue": {**queue, "items": [{
            "run_id": q.get("run_id"), "kind": q.get("kind"), "status": q.get("status"),
            "workspace": q.get("workspace"), "execution_mode": q.get("execution_mode"),
            "priority": q.get("priority"), "lease_owner": q.get("lease_owner"),
            "attempts": q.get("attempts"), "created_at": q.get("created_at"),
            "last_error": q.get("last_error"),
        } for q in queued]},
        "outbox": outbox,
        "runs": runs,
        "flow_runs": flows,
        "loops": loops,
        "nodes": nodes,
        "containers": containers,
        "hosts": [{
            "host": h,
            "members": [m["member_id"] for m in member_rows if str(m.get("host") or "") == h
                        and m.get("status") != "stopped"],
            "runs": _by_host(runs).get(h, 0),
            "flow_runs": _by_host(flows).get(h, 0),
            "nodes": _by_host(nodes).get(h, 0),
            "containers": _by_host(containers).get(h, 0),
        } for h in hosts],
    }


@router.get("")
async def deployment_map():
    """The map: members, leases, queue, and the working entities by host."""
    import asyncio
    return await asyncio.to_thread(build_map)


@router.get("/members")
async def members_list():
    from common import members
    return {"members": members.list_members()}


@router.get("/members/{member_id}/logs", response_class=PlainTextResponse)
async def member_logs(member_id: str, tail: int = 500):
    """The last lines of a member's own log (its host's file, or the
    object-store mirror when the member runs elsewhere)."""
    from common import members
    text = members.read_log(member_id, tail=tail)
    if text is None:
        if members.get(member_id) is None:
            raise HTTPException(status_code=404, detail="Unknown member")
        return "(no log yet)"
    return text


@router.delete("/members/{member_id}")
async def forget_member(member_id: str):
    """Drop a row a process left behind. Refused for a live one: the map
    must never lose a process that is still beating."""
    from common import members
    rec = members.get(member_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown member")
    if rec.get("status") == "live":
        raise HTTPException(status_code=409, detail="That member is still alive")
    members.forget(member_id)
    return {"forgotten": member_id}
