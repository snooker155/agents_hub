"""
A service's replicas: the resident instances that realise its desired state.

Starting and stopping go through instances/carrier.py like any resident
instance; what this module adds is the service's view of them: which are
live, how loaded each one is, which one a conversation belongs to, and the
choice of the replica a new message goes to (:func:`pick`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db
from instances import carrier, inbox, store as istore
from services import store

log = logging.getLogger(__name__)


class ServiceUnavailable(RuntimeError):
    """No replica can take the message: the service is paused, or it has no
    replica and may not start one."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_replicas(service_id: str, *, live: Optional[bool] = None,
                  limit: int = 500) -> List[Dict[str, Any]]:
    page = istore.list_instances(limit=limit, service_id=service_id, live=live,
                                 kinds=istore.CARRIER_KINDS)
    return [carrier.sync(i) or i for i in page["items"]]


def get_replica(instance_id: str) -> Optional[Dict[str, Any]]:
    """One replica by its instance id, synced with its carrier, or None when
    the id names no instance or an instance that belongs to no service (the
    terminal, common/terminal.py, opens only on replicas through here)."""
    replica = istore.get(str(instance_id))
    if replica is None or not replica.get("service_id"):
        return None
    return carrier.sync(replica) or replica


def live_replicas(service: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in list_replicas(service["service_id"], live=True)
            if r.get("state") in istore.LIVE_STATES]


def running_counts(instance_ids: List[str]) -> Dict[str, int]:
    """Open runs per replica, one grouped query."""
    ids = [str(i) for i in instance_ids if i]
    if not ids:
        return {}
    rows = db.get_conn().execute(
        "SELECT instance_id, COUNT(*) AS n FROM runs WHERE status IN ('running', 'stop') "
        f"AND instance_id IN ({', '.join('?' * len(ids))}) GROUP BY instance_id", ids,
    ).fetchall()
    return {str(r["instance_id"]): int(r["n"]) for r in rows}


def loads(replicas: List[Dict[str, Any]]) -> Dict[str, int]:
    """Runs in progress plus mail waiting, per replica: what a new message
    would queue behind."""
    ids = [str(r["instance_id"]) for r in replicas]
    running = running_counts(ids)
    pending = inbox.pending_counts(ids)
    return {i: running.get(i, 0) + pending.get(i, 0) for i in ids}


def conversation_holder(service_id: str, replica_ids: List[str],
                        conversation_id: Optional[str]) -> Optional[str]:
    """The replica already answering ``conversation_id``: one with an open
    run of it, else one holding an undelivered message of it. Two messages of
    a conversation are answered one after the other, and only one replica
    can keep that promise."""
    if not replica_ids:
        return None
    cid = inbox.normalize_conversation(conversation_id)
    conv_sql = "conversation_id IS NULL" if cid is None else "conversation_id = ?"
    params: List[Any] = [str(service_id)] + ([] if cid is None else [cid])
    row = db.get_conn().execute(
        f"SELECT instance_id FROM runs WHERE service_id = ? AND {conv_sql} "
        "AND status IN ('running', 'stop') ORDER BY started_at DESC LIMIT 1", params,
    ).fetchone()
    if row is not None and str(row["instance_id"] or "") in replica_ids:
        return str(row["instance_id"])
    return inbox.pending_holder(replica_ids, conversation_id)


def is_stale(replica: Dict[str, Any]) -> bool:
    """Whether ``replica`` was started from other code than this process runs
    (common/code_version.py). One started before replicas were stamped has no
    stamp and counts as stale."""
    from common import code_version
    return replica.get("carrier_code") != code_version.current()


def idle_seconds(replica: Dict[str, Any]) -> float:
    ts = replica.get("last_activity_at") or replica.get("started_at")
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - dt).total_seconds())


def _replica_seq(service_id: str) -> int:
    row = db.get_conn().execute(
        "SELECT COUNT(*) FROM instances WHERE service_id = ?", (str(service_id),)).fetchone()
    return int(row[0] or 0) + 1


def start_replica(service: Dict[str, Any], *, reason: str = "scale up") -> Dict[str, Any]:
    """Start one more replica of ``service`` (a resident instance carrying its
    id). Raises what carrier.start raises; the failure is journaled first."""
    sid = str(service["service_id"])
    label = f"{service.get('name') or service.get('agent_id') or 'runner'} #{_replica_seq(sid)}"
    try:
        replica = carrier.start(
            service.get("agent_id"),
            workspace=service.get("workspace"),
            label=label,
            environment_id=service.get("environment_id"),
            take_tasks=bool(service.get("take_tasks")),
            concurrency=int(service.get("concurrency") or 4),
            direct_port=False,
            started_by=service.get("created_by"),
            service_id=sid,
            check_capacity=False,
        )
    except Exception as exc:
        store.add_event(sid, "replica_failed", f"{reason}: {exc}")
        raise
    store.add_event(sid, "replica_started", f"{reason}: {label}", replica["instance_id"])
    return replica


def stop_replica(service: Dict[str, Any], replica: Dict[str, Any], *, reason: str) -> bool:
    sid = str(service["service_id"])
    iid = str(replica["instance_id"])
    stopped = carrier.stop(iid, reason=f"service: {reason}")
    store.add_event(sid, "replica_stopped" if stopped else "replica_stop_failed",
                    f"{reason}: {replica.get('label') or iid}", iid)
    return stopped


def pick(service: Dict[str, Any], *, conversation_id: Optional[str] = None,
         allow_start: bool = True) -> Dict[str, Any]:
    """The replica a new message of ``conversation_id`` goes to.

    In order: the replica already holding the conversation; the least loaded
    live replica with a free slot; a new replica when the service may still
    grow (``allow_start``); else the least loaded live one, where the message
    waits. Raises :class:`ServiceUnavailable` when there is no replica and
    none may be started (a paused service, or a maximum of zero).

    A replica on old code (:func:`is_stale`) gets a new conversation only
    when no current one has a free slot and none may be started: the
    supervisor replaces it once it is idle.
    """
    if service.get("status") != store.STATUS_ACTIVE:
        raise ServiceUnavailable(f"Service '{service.get('name')}' is paused")
    replicas = live_replicas(service)
    ids = [str(r["instance_id"]) for r in replicas]
    holder = conversation_holder(str(service["service_id"]), ids, conversation_id)
    if holder:
        return next(r for r in replicas if str(r["instance_id"]) == holder)
    load = loads(replicas)
    concurrency = int(service.get("concurrency") or 4)
    ranked = sorted(replicas, key=lambda r: (load.get(str(r["instance_id"]), 0),
                                             r.get("state") != "standby"))
    stale = {str(r["instance_id"]) for r in replicas if is_stale(r)}
    free = [r for r in ranked if load.get(str(r["instance_id"]), 0) < concurrency]
    for r in free:
        if str(r["instance_id"]) not in stale:
            return r
    if allow_start and len(replicas) < int(service.get("replicas_max") or 0):
        return start_replica(service, reason="on demand")
    if free:
        return free[0]
    if ranked:
        return ranked[0]
    raise ServiceUnavailable(
        f"Service '{service.get('name')}' has no replica and may not start one "
        f"(replicas_max={service.get('replicas_max')})")


def summary(service_id: str) -> Dict[str, int]:
    """Replica counts by state, for lists and the service page."""
    rows = db.get_conn().execute(
        "SELECT state, COUNT(*) AS n FROM instances WHERE service_id = ? AND archived_at IS NULL "
        "GROUP BY state", (str(service_id),)).fetchall()
    counts = {s: 0 for s in istore.ALL_STATES}
    for r in rows:
        counts[str(r["state"] or "")] = int(r["n"])
    counts["live"] = sum(counts.get(s, 0) for s in istore.LIVE_STATES)
    counts["total"] = sum(int(r["n"]) for r in rows)
    return counts
