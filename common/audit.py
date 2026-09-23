"""
The audit log: who did what, when, to which object. Append-only.

Two kinds of writer. The middleware in ``dashboard/backend/main.py`` records
every write request under ``/api`` (method, path, status, the principal) when
:func:`requests_enabled` says so: on in ``token`` and ``multi`` mode by
default, off for the single operator, who has nobody to be audited for. The
key points record themselves in every mode through :func:`record`: a login
and a logout, a role or membership change, an agent launch, a tool approval,
a workspace policy change, a budget change.

A row is never updated or deleted by the application; only
:func:`prune` removes rows older than ``AUDIT_RETENTION_DAYS`` (0 keeps all).

Every row is also offered to the webhook endpoints of its workspace that
subscribe to the ``audit`` event (docs/notifications.md), so a SIEM can
collect it without reading the database. A row with no workspace goes to
the endpoints of the ``default`` workspace, which is where an installation's
global integrations live.

Writing must never break the request it describes: :func:`record` swallows
every error and logs it, because an audit failure is a monitoring problem,
not a reason to refuse the action.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from common import db

log = logging.getLogger(__name__)

#: The webhook event name a row is delivered as.
EVENT_NAME = "audit"

#: Paths the request logger skips: the relays a run's own subprocess makes
#: (dozens per second, all as the service principal), the live stream, and
#: the self-authenticating ingest. These describe machinery, not people.
SKIP_PREFIXES = (
    "/api/run-state", "/api/stream", "/api/ingest", "/api/sessions/",
    "/api/instances/", "/api/chat/message", "/api/page-chat", "/api/views/",
    "/api/audit",
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def requests_enabled(mode: Optional[str] = None) -> bool:
    """Whether the middleware records write requests in the current mode."""
    from common.config import settings
    value = str(getattr(settings, "audit_requests", "auto") or "auto").strip().lower()
    if value in ("1", "true", "on", "yes"):
        return True
    if value in ("0", "false", "off", "no"):
        return False
    if mode is None:
        from common.identity import current_mode
        mode = current_mode()
    return mode != "single"


def should_log_request(method: str, path: str, principal: Any = None) -> bool:
    """The request-logger's filter: writes under ``/api`` by people, not relays."""
    method = (method or "").upper()
    if method in _SAFE_METHODS:
        return False
    if not path.startswith("/api"):
        return False
    for prefix in SKIP_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return False
    if principal is not None and getattr(principal, "kind", "") == "service":
        return False
    return True


def actor_fields(principal: Any) -> Dict[str, Optional[str]]:
    """The three actor columns from a principal (or None: the system itself)."""
    if principal is None:
        return {"actor_id": None, "actor_kind": "system", "actor_name": None}
    return {
        "actor_id": getattr(principal, "id", None),
        "actor_kind": getattr(principal, "via", "") or getattr(principal, "kind", None),
        "actor_name": getattr(principal, "username", None),
    }


def record(action: str, *, principal: Any = None, actor: Optional[Dict[str, Any]] = None,
           object_type: Optional[str] = None, object_id: Optional[str] = None,
           workspace: Optional[str] = None, ip: Optional[str] = None,
           method: Optional[str] = None, path: Optional[str] = None,
           result: str = "ok", details: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Append one row and offer it to the ``audit`` webhooks. Never raises.

    ``principal`` is the usual way to say who; ``actor`` (the three columns
    as a dict) is for callers that only have ids, such as a login that failed
    before there was a principal. Returns the row id, or None when writing
    failed.
    """
    fields = dict(actor or actor_fields(principal))
    at = _now().isoformat()
    payload = json.dumps(details or {}, ensure_ascii=False, default=str)
    try:
        with db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO audit_log (at, actor_id, actor_kind, actor_name, action, "
                "object_type, object_id, workspace, ip, method, path, result, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (at, fields.get("actor_id"), fields.get("actor_kind"),
                 fields.get("actor_name"), str(action), object_type,
                 str(object_id) if object_id is not None else None, workspace, ip,
                 method, path, str(result), payload),
            )
            row_id = getattr(cursor, "lastrowid", None)
            if not row_id and db.is_postgres():
                row = conn.execute("SELECT MAX(id) FROM audit_log").fetchone()
                row_id = row[0] if row else None
    except Exception:
        log.warning("audit: could not record %s", action, exc_info=True)
        return None
    entry = {
        "id": row_id, "at": at, **fields, "action": str(action),
        "object_type": object_type, "object_id": object_id, "workspace": workspace,
        "ip": ip, "method": method, "path": path, "result": str(result),
        "details": details or {},
    }
    _fan_out(entry)
    return row_id


def _fan_out(entry: Dict[str, Any]) -> None:
    """Hand the row to every ``audit`` webhook of its workspace."""
    try:
        from notify import outbound as notify_outbound
        from notify import store as notify_store
        workspace = entry.get("workspace") or "default"
        endpoints = notify_store.endpoints_for_event(workspace, EVENT_NAME)
        if not endpoints:
            return
        event = {
            "id": f"audit-{entry.get('id')}",
            "type": EVENT_NAME,
            "workspace": workspace,
            "created_at": entry["at"],
            "data": entry,
        }
        for endpoint in endpoints:
            notify_outbound.dispatch(endpoint, event)
    except Exception:
        log.debug("audit: webhook fan-out failed", exc_info=True)


def _row_to_entry(row) -> Dict[str, Any]:
    try:
        details = json.loads(row["details"] or "{}")
    except (TypeError, ValueError):
        details = {}
    return {
        "id": row["id"], "at": row["at"], "actor_id": row["actor_id"],
        "actor_kind": row["actor_kind"], "actor_name": row["actor_name"],
        "action": row["action"], "object_type": row["object_type"],
        "object_id": row["object_id"], "workspace": row["workspace"], "ip": row["ip"],
        "method": row["method"], "path": row["path"], "result": row["result"],
        "details": details,
    }


def query(*, actor: Optional[str] = None, action: Optional[str] = None,
          workspace: Optional[str] = None, workspaces: Optional[List[str]] = None,
          object_type: Optional[str] = None, object_id: Optional[str] = None,
          since: Optional[str] = None, until: Optional[str] = None,
          text: Optional[str] = None, result: Optional[str] = None,
          limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    """A page of rows, newest first: ``{items, total, limit, offset}``.

    ``action`` matches a prefix (``auth.`` selects every auth event);
    ``workspaces`` restricts to a list (an owner reading their own
    workspaces); ``text`` is a substring over path, object id and details.
    """
    where: List[str] = []
    args: List[Any] = []
    if actor:
        where.append("(actor_id = ? OR actor_name = ?)")
        args += [actor, actor]
    if action:
        where.append("action LIKE ?")
        args.append(f"{action}%")
    if workspace:
        where.append("workspace = ?")
        args.append(workspace)
    if workspaces is not None:
        if not workspaces:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        where.append("workspace IN (" + ",".join("?" for _ in workspaces) + ")")
        args += list(workspaces)
    if object_type:
        where.append("object_type = ?")
        args.append(object_type)
    if object_id:
        where.append("object_id = ?")
        args.append(str(object_id))
    if since:
        where.append("at >= ?")
        args.append(since)
    if until:
        where.append("at <= ?")
        args.append(until)
    if result:
        where.append("result = ?")
        args.append(result)
    if text:
        where.append("(path LIKE ? OR object_id LIKE ? OR details LIKE ? OR action LIKE ?)")
        args += [f"%{text}%"] * 4
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = db.get_conn()
    total = int(conn.execute(f"SELECT COUNT(*) FROM audit_log{clause}", tuple(args)).fetchone()[0])
    limit = max(1, min(int(limit), 5000))
    offset = max(0, int(offset))
    rows = conn.execute(
        f"SELECT * FROM audit_log{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(args) + (limit, offset)).fetchall()
    return {"items": [_row_to_entry(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


def actions() -> List[str]:
    """Every distinct action recorded so far, for a filter dropdown."""
    rows = db.get_conn().execute(
        "SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()
    return [str(r[0]) for r in rows]


def prune(retention_days: Optional[int] = None) -> int:
    """Drop rows older than the retention window. Returns how many went."""
    if retention_days is None:
        from common.config import settings
        retention_days = int(getattr(settings, "audit_retention_days", 365) or 0)
    if retention_days <= 0:
        return 0
    cutoff = (_now() - timedelta(days=retention_days)).isoformat()
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM audit_log WHERE at < ?", (cutoff,))
    return int(cursor.rowcount or 0)


__all__ = [
    "EVENT_NAME", "SKIP_PREFIXES", "actions", "actor_fields", "prune", "query",
    "record", "requests_enabled", "should_log_request",
]
