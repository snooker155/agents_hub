"""
The hub-wide MCP allowlist (migration 0026, docs/registry.md).

Separate from ``mcp_client.store`` on purpose. A store record is what a
*workspace* attached — its own copy of the connection details, editable by
anyone who can reach that workspace. A catalog entry is what an *admin* has
vetted for the whole hub. ``AGENTS_HUB_MCP_ALLOWLIST_ONLY`` (default off) is
the switch that makes the second gate the first: on, a workspace server that
does not match an approved entry cannot be attached or edited
(``dashboard/backend/routes/mcp.py``) and produces no tools
(``mcp_client.client.tools_for``), whatever a workspace's own record says.

A server "matches" a catalog entry when they share an id and the same way of
reaching the process: for stdio, the same command and arguments; otherwise
the same URL. The transport itself must agree too — a stdio entry does not
approve an http server that happens to reuse its id.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

STATUSES = ("requested", "approved", "blocked")

_COLUMNS = ("id", "name", "description", "transport", "command", "args", "url",
            "capabilities", "owner_user", "status", "note", "reviewed_by",
            "created_at", "updated_at", "reviewed_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def allowlist_only() -> bool:
    """Whether an unapproved MCP server may not be attached, edited or loaded.

    Resolved live from .env like every other hub toggle (see
    ``agents.registry.registry_review_required``), so the Settings/registry
    page change takes effect on the next request, not the next restart.
    """
    from common.config import live_setting
    return live_setting("AGENTS_HUB_MCP_ALLOWLIST_ONLY", "false").strip().lower() in (
        "1", "true", "yes", "on")


def _row(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "transport": row["transport"] or "stdio",
        "command": row["command"] or "",
        "args": list(db.loads(row["args"], []) or []),
        "url": row["url"] or "",
        "capabilities": dict(db.loads(row["capabilities"], {}) or {}),
        "owner_user": row["owner_user"],
        "status": row["status"] or "requested",
        "note": row["note"] or "",
        "reviewed_by": row["reviewed_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "reviewed_at": row["reviewed_at"],
    }


def list_all(status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM mcp_catalog WHERE status = ? ORDER BY created_at DESC",
            (str(status),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mcp_catalog ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def get(catalog_id: str) -> Optional[Dict[str, Any]]:
    if not catalog_id:
        return None
    row = db.get_conn().execute(
        "SELECT * FROM mcp_catalog WHERE id = ?", (str(catalog_id),)).fetchone()
    return _row(row) if row else None


def create(data: Dict[str, Any], *, owner_user: Optional[str], is_admin: bool) -> Dict[str, Any]:
    """Add a catalog entry. Only an admin may create one already ``approved``;
    anyone else's request is always filed as ``requested``, whatever status
    they passed in."""
    from mcp_client.store import validate_id, validate_url, TRANSPORTS

    catalog_id = validate_id(str(data.get("id") or ""))
    if get(catalog_id) is not None:
        raise ValueError(f"A catalog entry with id '{catalog_id}' already exists")
    transport = str(data.get("transport") or "stdio").strip().lower()
    if transport not in TRANSPORTS:
        transport = "stdio"
    url = str(data.get("url") or "").strip()
    validate_url(transport, url)
    requested_status = str(data.get("status") or "requested").strip().lower()
    status = requested_status if (is_admin and requested_status in STATUSES) else "requested"
    now = _now()
    record = {
        "id": catalog_id,
        "name": str(data.get("name") or catalog_id),
        "description": str(data.get("description") or ""),
        "transport": transport,
        "command": str(data.get("command") or ""),
        "args": [str(a) for a in (data.get("args") or [])],
        "url": url,
        "capabilities": {k: bool(v) for k, v in (data.get("capabilities") or {}).items()
                        if k in ("ingests_untrusted", "reads_private", "can_exfiltrate")},
        "owner_user": owner_user,
        "status": status,
        "note": str(data.get("note") or ""),
        "reviewed_by": owner_user if status == "approved" else None,
        "created_at": now,
        "updated_at": now,
        "reviewed_at": now if status in ("approved", "blocked") else None,
    }
    with db.transaction() as conn:
        conn.execute(
            f"INSERT INTO mcp_catalog ({', '.join(_COLUMNS)}) VALUES "
            f"({', '.join('?' for _ in _COLUMNS)})",
            (record["id"], record["name"], record["description"], record["transport"],
             record["command"], json.dumps(record["args"]), record["url"],
             json.dumps(record["capabilities"]), record["owner_user"], record["status"],
             record["note"], record["reviewed_by"], record["created_at"],
             record["updated_at"], record["reviewed_at"]))
    return record


def set_status(catalog_id: str, status: str, *, reviewed_by: Optional[str],
              note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Approve or block an entry. Returns None when the id is unknown."""
    if status not in ("approved", "blocked"):
        raise ValueError(f"'{status}' is not a status a review can set")
    if get(catalog_id) is None:
        return None
    now = _now()
    with db.transaction() as conn:
        if note is not None:
            conn.execute(
                "UPDATE mcp_catalog SET status = ?, reviewed_by = ?, reviewed_at = ?, "
                "note = ?, updated_at = ? WHERE id = ?",
                (status, reviewed_by, now, note, now, str(catalog_id)))
        else:
            conn.execute(
                "UPDATE mcp_catalog SET status = ?, reviewed_by = ?, reviewed_at = ?, "
                "updated_at = ? WHERE id = ?",
                (status, reviewed_by, now, now, str(catalog_id)))
    return get(catalog_id)


def delete(catalog_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM mcp_catalog WHERE id = ?", (str(catalog_id),))
        return bool(getattr(cur, "rowcount", 0))


def matches(record: Dict[str, Any]) -> bool:
    """Whether a workspace-attached server record matches an *approved*
    catalog entry: same id, same transport, and the same command+args (stdio)
    or url (everything else). Never raises; an id the catalog does not have
    at all simply does not match."""
    entry = get(str((record or {}).get("id") or ""))
    if not entry or entry.get("status") != "approved":
        return False
    if entry.get("transport") != (record or {}).get("transport"):
        return False
    if entry.get("transport") == "stdio":
        return (entry.get("command") == (record or {}).get("command")
                and list(entry.get("args") or []) == list((record or {}).get("args") or []))
    return entry.get("url") == (record or {}).get("url")


__all__ = [
    "STATUSES", "allowlist_only", "create", "delete", "get", "list_all",
    "matches", "set_status",
]
