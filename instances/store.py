"""
Instance persistence — the ``instances`` table.

Every query here is SQL-filtered and paginated. That is the point of the table
existing at all: a workspace can hold thousands of instances (one per task run),
so nothing in this module may load the whole set into Python to filter it.

State machine::

    starting ──► active ◄──► standby ──► finished
        │          │            │           │
        └──────────┴────────────┴──► stopped / failed

- ``active``   — an open run is executing right now
- ``standby``  — the carrier process is alive but idle (a node in its poll loop)
- ``finished`` — no process left, context retained; messaging it revives it
- ``stopped`` / ``failed`` — terminal

Retention: terminal instances beyond ``RETENTION_KEEP`` per workspace are
archived (``archived_at`` set). Archived rows drop out of the default listing
but stay reachable by id, so a permalink never 404s.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from common import db

# Columns stored individually; anything else a caller passes lands in ``extra``.
INSTANCE_COLUMNS: Tuple[str, ...] = (
    "instance_id", "agent_id", "workspace", "project_id", "kind", "state",
    "label", "session_id", "node_id", "container_name", "pid",
    "current_run_id", "task_id", "provider", "model",
    "created_at", "started_at", "last_activity_at", "finished_at",
    "archived_at", "runs_count", "total_tokens", "total_duration_ms",
    "last_activity", "error",
)

LIVE_STATES = ("starting", "active", "standby")
TERMINAL_STATES = ("finished", "stopped", "failed")
ALL_STATES = LIVE_STATES + TERMINAL_STATES

KINDS = ("node", "container", "task", "chat", "flow_node", "team_member")

# How many terminal instances a workspace keeps in its listing before older ones
# are archived. Archived instances stay in the table and stay reachable by id.
RETENTION_KEEP = 500


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_instance_id() -> str:
    return "inst_" + uuid.uuid4().hex[:16]


def _row_to_dict(row) -> Dict[str, Any]:
    rec: Dict[str, Any] = {k: row[k] for k in INSTANCE_COLUMNS}
    extra = db.loads(row["extra"], {}) or {}
    if isinstance(extra, dict):
        rec.update(extra)
    rec["is_live"] = rec.get("state") in LIVE_STATES
    return rec


def _write(conn, merged: Dict[str, Any]) -> None:
    data = dict(merged)
    cols = {k: data.pop(k, None) for k in INSTANCE_COLUMNS}
    data.pop("is_live", None)
    conn.execute(
        f"INSERT OR REPLACE INTO instances ({', '.join(INSTANCE_COLUMNS)}, extra) "
        f"VALUES ({', '.join('?' * len(INSTANCE_COLUMNS))}, ?)",
        [cols[k] for k in INSTANCE_COLUMNS] + [db.dumps(data)],
    )


# ── Reads ────────────────────────────────────────────────────────────────────

def get(instance_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM instances WHERE instance_id = ?", (str(instance_id),)
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def get_by_session(session_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM instances WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (str(session_id),),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def get_by_node(node_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM instances WHERE node_id = ? ORDER BY created_at DESC LIMIT 1",
        (str(node_id),),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def _where(
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    kind: Optional[str] = None,
    state: Optional[str] = None,
    live: Optional[bool] = None,
    node_id: Optional[str] = None,
    task_id: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if workspace:
        # Instances with no workspace recorded belong to "default", mirroring
        # how the Messages list treats runs.
        if workspace == "default":
            clauses.append("(workspace IS NULL OR workspace = '' OR workspace = 'default')")
        else:
            clauses.append("workspace = ?")
            params.append(workspace)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if state:
        clauses.append("state = ?")
        params.append(state)
    if live is True:
        clauses.append(f"state IN ({', '.join('?' * len(LIVE_STATES))})")
        params.extend(LIVE_STATES)
    elif live is False:
        clauses.append(f"state IN ({', '.join('?' * len(TERMINAL_STATES))})")
        params.extend(TERMINAL_STATES)
    if node_id:
        clauses.append("node_id = ?")
        params.append(node_id)
    if task_id:
        clauses.append("task_id = ?")
        params.append(str(task_id))
    if q:
        clauses.append("(label LIKE ? OR agent_id LIKE ? OR instance_id LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if not include_archived:
        clauses.append("archived_at IS NULL")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_instances(limit: int = 100, offset: int = 0, **filters) -> Dict[str, Any]:
    """A page of instances, newest activity first, plus the total match count.

    Returns ``{items, total, limit, offset}``. Live instances sort ahead of
    terminal ones so an operator watching 1000 copies sees the working ones
    without paging.
    """
    where, params = _where(**filters)
    conn = db.get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM instances{where}", params).fetchone()[0]
    live_rank = f"CASE WHEN state IN ({', '.join('?' * len(LIVE_STATES))}) THEN 0 ELSE 1 END"
    rows = conn.execute(
        f"SELECT * FROM instances{where} "
        f"ORDER BY {live_rank}, COALESCE(last_activity_at, started_at, created_at) DESC "
        "LIMIT ? OFFSET ?",
        list(params) + list(LIVE_STATES) + [int(limit), int(offset)],
    ).fetchall()
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
    }


def counts_by_state(workspace: Optional[str] = None, agent_id: Optional[str] = None
                    ) -> Dict[str, int]:
    """Per-state totals for the header strip — one grouped query, not N."""
    where, params = _where(workspace=workspace, agent_id=agent_id)
    rows = db.get_conn().execute(
        f"SELECT state, COUNT(*) AS n FROM instances{where} GROUP BY state", params
    ).fetchall()
    counts = {s: 0 for s in ALL_STATES}
    for r in rows:
        counts[str(r["state"] or "")] = int(r["n"])
    counts["live"] = sum(counts.get(s, 0) for s in LIVE_STATES)
    counts["total"] = sum(int(r["n"]) for r in rows)
    return counts


def counts_by_agent(workspace: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """``{agent_id: {live, total}}`` for the agent list's running-copies badge."""
    where, params = _where(workspace=workspace)
    rows = db.get_conn().execute(
        f"SELECT agent_id, state, COUNT(*) AS n FROM instances{where} "
        "GROUP BY agent_id, state", params
    ).fetchall()
    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        agent = str(r["agent_id"] or "")
        bucket = out.setdefault(agent, {"live": 0, "total": 0})
        bucket["total"] += int(r["n"])
        if r["state"] in LIVE_STATES:
            bucket["live"] += int(r["n"])
    return out


# ── Writes ───────────────────────────────────────────────────────────────────

def create(
    agent_id: str,
    *,
    instance_id: Optional[str] = None,
    kind: str = "task",
    workspace: Optional[str] = None,
    state: str = "starting",
    **fields,
) -> Dict[str, Any]:
    now = utc_iso()
    rec: Dict[str, Any] = {
        "instance_id": instance_id or new_instance_id(),
        "agent_id": agent_id,
        "workspace": workspace,
        "kind": kind,
        "state": state,
        "created_at": now,
        "started_at": now,
        "last_activity_at": now,
        "runs_count": 0,
        "total_tokens": 0,
        "total_duration_ms": 0,
    }
    rec.update({k: v for k, v in fields.items() if v is not None})
    rec.setdefault("label", "")
    with db.transaction() as conn:
        _write(conn, rec)
    return rec


def update(instance_id: str, **updates) -> Optional[Dict[str, Any]]:
    """Merge ``updates`` into one instance row. Returns the new record, or None
    when the instance does not exist."""
    if not updates:
        return get(instance_id)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE instance_id = ?", (str(instance_id),)
        ).fetchone()
        if row is None:
            return None
        merged = {**_row_to_dict(row), **{k: v for k, v in updates.items() if v is not None}}
        # Explicit None is meaningful for these — clearing a finished run.
        for k in ("current_run_id", "task_id", "error"):
            if k in updates and updates[k] is None:
                merged[k] = None
        merged["instance_id"] = str(instance_id)
        _write(conn, merged)
        return merged


def touch(instance_id: str, activity: Optional[str] = None, **updates) -> Optional[Dict[str, Any]]:
    """Mark the instance as having just done something."""
    payload = {"last_activity_at": utc_iso(), **updates}
    if activity:
        payload["last_activity"] = activity[:300]
    return update(instance_id, **payload)


def add_run_stats(instance_id: str, *, tokens: int = 0, duration_ms: int = 0) -> None:
    """Fold one finished run's stats into the instance totals (atomic)."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE instances SET runs_count = COALESCE(runs_count, 0) + 1, "
            "total_tokens = COALESCE(total_tokens, 0) + ?, "
            "total_duration_ms = COALESCE(total_duration_ms, 0) + ?, "
            "last_activity_at = ? WHERE instance_id = ?",
            (int(tokens or 0), int(duration_ms or 0), utc_iso(), str(instance_id)),
        )


def delete(instance_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM instances WHERE instance_id = ?", (str(instance_id),))
        conn.execute("DELETE FROM instance_inbox WHERE instance_id = ?", (str(instance_id),))
        # Runs survive — they are the journal and stay readable in Messages.
        conn.execute("UPDATE runs SET instance_id = NULL WHERE instance_id = ?", (str(instance_id),))
        return cur.rowcount > 0


def enforce_retention(workspace: Optional[str], keep: int = RETENTION_KEEP) -> int:
    """Archive terminal instances beyond the newest ``keep`` in a workspace.

    Archiving only sets ``archived_at``: the row, its runs and its context stay
    intact, so an old permalink still opens. Returns how many were archived.
    """
    where, params = _where(workspace=workspace, live=False)
    with db.transaction() as conn:
        rows = conn.execute(
            f"SELECT instance_id FROM instances{where} "
            "ORDER BY COALESCE(finished_at, last_activity_at, created_at) DESC "
            "LIMIT -1 OFFSET ?",
            list(params) + [int(keep)],
        ).fetchall()
        if not rows:
            return 0
        now = utc_iso()
        conn.executemany(
            "UPDATE instances SET archived_at = ? WHERE instance_id = ?",
            [(now, r["instance_id"]) for r in rows],
        )
        return len(rows)


def workspaces_with_instances() -> List[str]:
    """Distinct workspaces that own at least one instance — the retention sweep's
    work list, so it never has to enumerate workspaces that have none."""
    rows = db.get_conn().execute(
        "SELECT DISTINCT COALESCE(NULLIF(workspace, ''), 'default') AS ws FROM instances"
    ).fetchall()
    return [str(r["ws"]) for r in rows]


# ── Journal (runs belonging to an instance) ──────────────────────────────────

def runs_for(instance_id: str, limit: int = 50, offset: int = 0,
             ascending: bool = False) -> Dict[str, Any]:
    """A page of the instance's runs — its work journal."""
    conn = db.get_conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE instance_id = ?", (str(instance_id),)
    ).fetchone()[0]
    order = "ASC" if ascending else "DESC"
    rows = conn.execute(
        "SELECT run_id, agent_id, task_id, session_id, status, title, input, output, "
        "started_at, finished_at, error, log_file, prompt_tokens, completion_tokens, "
        "total_tokens, duration_ms, model, provider "
        f"FROM runs WHERE instance_id = ? ORDER BY COALESCE(started_at, created_at) {order} "
        "LIMIT ? OFFSET ?",
        (str(instance_id), int(limit), int(offset)),
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
    }
