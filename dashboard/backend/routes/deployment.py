"""
/api/cluster: the cluster map (formerly /api/deployment, kept as an alias).

One answer to "where is everything running, how busy is it, and is it
alive": the members (backend replicas and workers, from ``common/members.py``),
which of them holds which singleton role (``common/leases.py``), the launch
queue and the outbox, and the working entities grouped by the host they run
on: agent runs (with the age of their heartbeat), flow runs, loops (with the
replica executing them), resident instances and containers. ``/api/health``
stays the "is this process healthy" snapshot; this is the whole cluster map.

A member's own log is served here too, from its host's file or the
object-store mirror, and tailed live over the stream on
``logs:member:<member id>`` (common/live_state.py) like a resident instance's.

``entity_runs`` in the map is the one flow/loop/team/scenario query
(``common.entity_runs.list_runs(active=True)``), kind-agnostic: counts by kind
and status, plus every active run's kind, host, heartbeat age and resume
attempts. ``flow_runs`` and ``loops`` are kept alongside it, sliced from the
same query rather than a second one each, so the page that already reads them
keeps working unchanged.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

log = logging.getLogger(__name__)

# Every route below is registered on both of these: /api/cluster is the
# page's real name, /api/deployment stays live as an alias for anything, in or
# outside this repo, still calling the old path. main.py includes both.
# (Stacking two router decorators on one function is the FastAPI-supported way
# to expose one endpoint under two routers — a route decorator just registers
# the function and hands it back unchanged, so a second decorator on top adds
# a second registration rather than replacing the first.)
router = APIRouter(prefix="/api/cluster", tags=["cluster"])
deployment_alias_router = APIRouter(prefix="/api/deployment", tags=["deployment"])

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
        except Exception:  # noqa: BLE001 - one unreadable entry must not stop the rest of the listing
            log.debug("_active_runs: falling back after a failure", exc_info=True)
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


def _active_entity_runs() -> List[Dict[str, Any]]:
    """Every flow, loop, team and scenario run still going, kind-agnostic
    (common/entity_runs.py): the one query ``_active_flow_runs`` and
    ``_active_loops`` below both slice, and what the map's new ``entity_runs``
    section shows whole. Each row keeps its raw ``checkpoint`` (a loop's own
    position lives there) so those two slices do not need a second query;
    ``_entity_runs_section`` strips it back out before the map ships it."""
    try:
        from common import entity_runs
        recs = entity_runs.list_runs(active=True)
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_active_entity_runs: falling back after a failure", exc_info=True)
        return []
    out = []
    for rec in recs:
        out.append({
            "run_id": rec.get("run_id"), "kind": rec.get("kind"),
            "entity_id": rec.get("entity_id"), "task_id": rec.get("task_id"),
            "workspace": rec.get("workspace"), "status": rec.get("status"),
            "host": rec.get("host") or "", "pid": rec.get("pid"),
            "container_name": rec.get("container_name"),
            "started_at": rec.get("started_at"),
            "heartbeat_at": rec.get("heartbeat_at"),
            "heartbeat_age_seconds": _age(rec.get("heartbeat_at")),
            "resume_attempts": rec.get("resume_attempts"),
            "checkpoint": rec.get("checkpoint"),
        })
    return out


def _entity_runs_section(entity_active: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        from common import entity_runs
        counts = entity_runs.counts_by_kind()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_entity_runs_section: falling back after a failure", exc_info=True)
        counts = {}
    active = [{k: v for k, v in rec.items() if k != "checkpoint"} for rec in entity_active]
    return {"counts_by_kind": counts, "active": active}


def _active_flow_runs(entity_active: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for rec in entity_active:
        if rec.get("kind") != "flow" or rec.get("status") not in ("running", "pending"):
            continue
        out.append({
            "flow_run_id": rec.get("run_id"), "flow_id": rec.get("entity_id"),
            "task_id": rec.get("task_id"), "workspace": rec.get("workspace"),
            "status": rec.get("status"), "host": rec.get("host") or "",
            "pid": rec.get("pid"), "started_at": rec.get("started_at"),
            "heartbeat_at": rec.get("heartbeat_at"),
            "heartbeat_age_seconds": rec.get("heartbeat_age_seconds"),
            "resume_attempts": rec.get("resume_attempts"),
        })
    return out


def _active_loops(entity_active: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for rec in entity_active:
        if rec.get("kind") != "loop" or rec.get("status") != "running":
            continue
        position = dict(rec.get("checkpoint") or {})
        out.append({
            "loop_run_id": rec.get("run_id"), "loop_id": rec.get("entity_id"),
            "workspace": rec.get("workspace"), "status": rec.get("status"),
            "owner": position.get("owner") or "", "host": rec.get("host") or "",
            "iterations_done": position.get("iterations_done"),
            "heartbeat_at": rec.get("heartbeat_at"),
            "heartbeat_age_seconds": rec.get("heartbeat_age_seconds"),
        })
    return out


def _instances() -> List[Dict[str, Any]]:
    try:
        from instances import carrier
        items = carrier.list_resident(live=True)
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_instances: falling back after a failure", exc_info=True)
        return []
    return [{
        "instance_id": i.get("instance_id"), "agent_id": i.get("agent_id"), "label": i.get("label"),
        "workspace": i.get("workspace"), "state": i.get("state"),
        "kind": i.get("kind"), "service_id": i.get("service_id"),
        "carrier_mode": i.get("carrier_mode"), "carrier_host": i.get("carrier_host") or "",
        "carrier_status": i.get("carrier_status"), "pid": i.get("pid"),
        "container_name": i.get("container_name"),
        "heartbeat_at": i.get("heartbeat_at"),
        "heartbeat_age_seconds": _age(i.get("heartbeat_at")),
        "started_at": i.get("started_at"),
    } for i in items]


def _services() -> List[Dict[str, Any]]:
    try:
        from routes.services import services_map
        return services_map()
    except Exception:  # noqa: BLE001 - the page shows no services rather than failing
        log.debug("deployment: could not list services", exc_info=True)
        return []


def _containers() -> List[Dict[str, Any]]:
    try:
        from managers import container_manager
        return container_manager.list_containers()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_containers: falling back after a failure", exc_info=True)
        return []


def _by_host(items: List[Dict[str, Any]], key: str = "host") -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for it in items:
        counts[str(it.get(key) or "")] += 1
    return dict(counts)


def build_map() -> Dict[str, Any]:
    """The whole picture, as one JSON document. Every part is best-effort:
    a store that cannot be read is reported empty, never as a failure of
    the map itself."""
    from common import leases, members, run_queue
    from common.config import hub_role

    try:
        member_rows = members.list_members()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("build_map: falling back after a failure", exc_info=True)
        member_rows = []
    try:
        lease_rows = leases.all_leases()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("build_map: falling back after a failure", exc_info=True)
        lease_rows = []
    by_owner: Dict[str, List[str]] = defaultdict(list)
    for lease in lease_rows:
        if not lease.get("expired"):
            by_owner[str(lease.get("owner") or "")].append(str(lease.get("role")))
    for m in member_rows:
        m["leases"] = sorted(by_owner.get(str(m.get("member_id")), []))

    runs = _active_runs()
    entity_active = _active_entity_runs()
    flows = _active_flow_runs(entity_active)
    loops = _active_loops(entity_active)
    entity_section = _entity_runs_section(entity_active)
    instances = _instances()
    containers = _containers()
    services = _services()

    try:
        queue = run_queue.stats()
        queued = run_queue.list_queue(statuses=[run_queue.STATUS_QUEUED, run_queue.STATUS_LEASED,
                                                run_queue.STATUS_RUNNING], limit=100)
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("build_map: falling back after a failure", exc_info=True)
        queue, queued = {}, []
    try:
        from notify import outbound
        outbox = outbound.stats()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("build_map: falling back after a failure", exc_info=True)
        outbox = {}

    hosts = sorted({str(m.get("host") or "") for m in member_rows}
                   | set(_by_host(runs)) | set(_by_host(instances, "carrier_host"))
                   | set(_by_host(containers)))
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
        "entity_runs": entity_section,
        "instances": instances,
        "services": services,
        "containers": containers,
        "hosts": [{
            "host": h,
            "members": [m["member_id"] for m in member_rows if str(m.get("host") or "") == h
                        and m.get("status") != "stopped"],
            "runs": _by_host(runs).get(h, 0),
            "flow_runs": _by_host(flows).get(h, 0),
            "instances": _by_host(instances, "carrier_host").get(h, 0),
            "containers": _by_host(containers).get(h, 0),
        } for h in hosts],
    }


@router.get("")
@deployment_alias_router.get("")
async def deployment_map():
    """The map: members, leases, queue, and the working entities by host."""
    import asyncio
    return await asyncio.to_thread(build_map)


@router.get("/members")
@deployment_alias_router.get("/members")
async def members_list():
    from common import members
    return {"members": members.list_members()}


@router.get("/members/{member_id}/logs", response_class=PlainTextResponse)
@deployment_alias_router.get("/members/{member_id}/logs", response_class=PlainTextResponse)
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
@deployment_alias_router.delete("/members/{member_id}")
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
