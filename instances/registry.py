"""
The single place every execution channel registers a live agent copy.

Chat, task subprocesses, node workers, containers and flow nodes all used to
report their existence in mutually incompatible ways — a node row here, a run
record there, a session document somewhere else. They now all call
:func:`ensure_instance`, so one query answers "what is running right now".

Lifecycle helpers publish a *delta* (see ``common.session_broker.notify_delta``)
rather than a list invalidation: with a thousand copies in flight, telling every
open tab to refetch the whole list is what makes the dashboard unusable.

Registration is idempotent. A caller that already knows its instance id (it came
down in the environment, or it is a node that is restarting) gets the existing
row back with its fields refreshed; nobody has to check first.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from common import db
from common.session_broker import notify_change, notify_delta
from instances import store

# Fields worth streaming on every change: enough for the list row to repaint
# without refetching, small enough to send a thousand of them.
DELTA_FIELDS = (
    "instance_id", "agent_id", "workspace", "kind", "state", "label",
    "current_run_id", "task_id", "node_id", "container_name", "session_id",
    "last_activity", "last_activity_at", "finished_at", "runs_count",
    "total_tokens", "total_duration_ms", "error",
)

# Environment variable carrying the instance id into a spawned agent process.
ENV_INSTANCE_ID = "AGENT_INSTANCE_ID"


def _publish(instance: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not instance:
        return instance
    delta = {k: instance.get(k) for k in DELTA_FIELDS}
    delta["is_live"] = instance.get("state") in store.LIVE_STATES
    notify_delta("instances", str(instance.get("instance_id") or ""), delta)
    return instance


def _next_seq(agent_id: str, workspace: Optional[str]) -> int:
    """Per-agent ordinal within a workspace, for a human-readable label.

    Counted rather than stored: ``idx_instances_agent`` makes it an index-only
    count, and instances are created far too rarely for it to matter.
    """
    row = db.get_conn().execute(
        "SELECT COUNT(*) FROM instances WHERE agent_id = ? AND "
        "COALESCE(NULLIF(workspace, ''), 'default') = ?",
        (str(agent_id), str(workspace or "default")),
    ).fetchone()
    return int(row[0] or 0) + 1


def deterministic_id(*parts: Any) -> str:
    """A stable instance id derived from what identifies the copy.

    Flow nodes and team seats have no id of their own to carry around, but they
    do have a natural key — (flow run, node) or (team run, seat). Deriving the
    id from it makes ``ensure_instance`` idempotent across rounds and retries
    without anyone having to thread an id through the call chain.
    """
    import hashlib
    raw = "|".join(str(p or "") for p in parts)
    return "inst_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_label(agent_id: str, seq: int, hint: Optional[str] = None) -> str:
    """``swe_agent #17 · refactor auth`` — the name an operator scans for in a
    list of a thousand identical copies."""
    base = f"{agent_id} #{seq}"
    hint = (hint or "").strip().replace("\n", " ")
    if not hint:
        return base
    if len(hint) > 60:
        hint = hint[:57] + "…"
    return f"{base} · {hint}"


def ensure_instance(
    agent_id: str,
    *,
    kind: str = "task",
    workspace: Optional[str] = None,
    instance_id: Optional[str] = None,
    session_id: Optional[str] = None,
    node_id: Optional[str] = None,
    container_name: Optional[str] = None,
    pid: Optional[int] = None,
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    label: Optional[str] = None,
    hint: Optional[str] = None,
    state: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    reuse_session: bool = False,
) -> Dict[str, Any]:
    """Return the instance for this execution context, creating it if new.

    Resolution order: explicit ``instance_id`` → the node's instance →
    (``reuse_session``) the session's instance → a new row. ``hint`` seeds the
    label (task title, first chat message) and is ignored once a label exists.

    ``reuse_session`` is what separates a conversation from a task: a chat
    instance spans every message of its conversation, while each task run is its
    own instance — the operator asked to see a thousand copies, not one row with
    a thousand runs hidden inside it.
    """
    # ``workspace`` is passed to store.create() positionally-by-name, so it is
    # deliberately not part of this dict.
    fields = {
        "session_id": session_id, "node_id": node_id,
        "container_name": container_name, "pid": pid, "task_id": task_id,
        "project_id": project_id, "provider": provider, "model": model,
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    existing = None
    if instance_id:
        existing = store.get(instance_id)
    if existing is None and node_id:
        existing = store.get_by_node(node_id)
    if existing is None and reuse_session and session_id:
        existing = store.get_by_session(session_id)
    if existing is not None:
        updates = dict(fields)
        if workspace:
            updates["workspace"] = workspace
        if state:
            updates["state"] = state
        if label:
            updates["label"] = label
        return _publish(store.update(existing["instance_id"], **updates)) or existing

    seq = _next_seq(agent_id, workspace)
    created = store.create(
        agent_id,
        instance_id=instance_id,
        kind=kind,
        workspace=workspace,
        state=state or "starting",
        label=label or build_label(agent_id, seq, hint),
        seq=seq,
        **fields,
    )
    _publish(created)
    # The list header and the agent badge count rows, so they do need to know a
    # row appeared; state changes afterwards travel as deltas.
    notify_change("instances")
    return created


# ── Lifecycle transitions ────────────────────────────────────────────────────

def mark_active(instance_id: str, run_id: Optional[str] = None,
                activity: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The instance started working on ``run_id``."""
    return _publish(store.touch(
        instance_id, activity, state="active",
        **({"current_run_id": run_id} if run_id else {}),
    ))


def mark_standby(instance_id: str, activity: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The carrier is alive but idle — a node back in its poll loop."""
    inst = store.update(instance_id, state="standby", last_activity_at=store.utc_iso(),
                        current_run_id=None,
                        **({"last_activity": activity[:300]} if activity else {}))
    return _publish(inst)


def mark_finished(instance_id: str, activity: Optional[str] = None
                  ) -> Optional[Dict[str, Any]]:
    """Work is over and no process remains — but the context is kept, so the
    instance can still be messaged and will come back with its history."""
    now = store.utc_iso()
    inst = store.update(instance_id, state="finished", finished_at=now,
                        last_activity_at=now, current_run_id=None,
                        **({"last_activity": activity[:300]} if activity else {}))
    inst = _publish(inst)
    if inst:
        try:
            store.enforce_retention(inst.get("workspace"))
        except Exception:
            pass
    return inst


def mark_stopped(instance_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = store.utc_iso()
    return _publish(store.update(instance_id, state="stopped", finished_at=now,
                                 last_activity_at=now, current_run_id=None,
                                 last_activity=(reason or "stopped")[:300]))


def mark_failed(instance_id: str, error: str) -> Optional[Dict[str, Any]]:
    now = store.utc_iso()
    return _publish(store.update(instance_id, state="failed", finished_at=now,
                                 last_activity_at=now, current_run_id=None,
                                 error=str(error)[:2000]))


def record_activity(instance_id: str, activity: str) -> Optional[Dict[str, Any]]:
    """A one-line "what it is doing right now" — the tool it just called."""
    return _publish(store.touch(instance_id, activity))


# ── Run ↔ instance linkage ───────────────────────────────────────────────────

def attach_run(run_id: str, instance_id: str) -> None:
    """Link an existing run record to its instance (the run is the journal entry)."""
    if not run_id or not instance_id:
        return
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET instance_id = ? WHERE run_id = ?",
                     (str(instance_id), str(run_id)))


def current_instance_id() -> Optional[str]:
    """The instance this process belongs to, when it was spawned as one."""
    return os.environ.get(ENV_INSTANCE_ID) or None


# ── Reconciliation ───────────────────────────────────────────────────────────

def reconcile() -> int:
    """Correct instances whose carrier died without telling us.

    A crash, a kill or a reboot between ``mark_active`` and ``mark_finished``
    leaves a row claiming to be alive forever. Returns how many were corrected.
    """
    from managers import run_manager as rm

    fixed = 0
    page = store.list_instances(limit=1000, live=True, include_archived=True)
    for inst in page["items"]:
        iid = inst["instance_id"]
        kind = inst.get("kind")
        if kind in ("node", "container"):
            if _node_alive(inst):
                continue
            mark_stopped(iid, "carrier node stopped")
            fixed += 1
            continue
        pid = int(inst.get("pid") or 0)
        if pid > 0 and not rm._pid_exists(pid):
            mark_finished(iid, "process exited")
            fixed += 1
    return fixed


def _node_alive(inst: Dict[str, Any]) -> bool:
    node_id = inst.get("node_id")
    if not node_id:
        return True  # nothing to check against; leave it alone
    try:
        from managers import node_manager
        node = node_manager.get_node(str(node_id))
    except Exception:
        return True
    if not node:
        return False
    return str(node.get("status") or "") in ("running", "starting", "stopping")


def live_ids_for_agent(agent_id: str, workspace: Optional[str] = None) -> List[str]:
    page = store.list_instances(limit=1000, agent_id=agent_id, workspace=workspace, live=True)
    return [i["instance_id"] for i in page["items"]]
