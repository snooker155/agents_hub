"""
Named, read-only database connections, one list per workspace.

Unlike the chat channels (one bot token per channel) or the issue trackers
(one base URL/token per connector), an operator can attach several databases
at once, so there is no single secret to hold in
:class:`connectors.channels.store.ChannelStore`'s ``config``. Instead the
whole list lives under the store's cursor key ``"connections"`` (cursor is
meant for transport bookkeeping, but it is just as good a place to keep a
JSON list the store already persists atomically), keyed by
``connectors/databases/__init__.py``'s ``STORE = ChannelStore("databases",
secret_fields=())``.

Each connection is a plain dict::

    {"id": uuid, "workspace": str, "name": str,
     "kind": "postgres"|"mysql"|"clickhouse"|"sqlite",
     "dsn": str (secret), "allowed_schemas": [str],
     "row_limit": int, "timeout_seconds": int, "created_at": iso}

The dsn is write-only: :func:`public` replaces it with a host-only hint
(``postgresql://db.internal:5432/app``, never the credentials in it), the
same write-only discipline ``ChannelStore.public_config`` applies to a
channel's secret fields, just done by hand here since the secret lives inside
a list item rather than in the store's flat config.

Everything that actually opens a connection lives in drivers.py, behind
guard.py's statement check; dashboard/backend/routes/databases.py and
tools/databases.py are the two callers of this module.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from connectors.channels.store import ChannelStore

KINDS: tuple[str, ...] = ("postgres", "mysql", "clickhouse", "sqlite")

DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 2000
DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 300

STORE = ChannelStore("databases", secret_fields=())

_SCHEME_PREFIXES: dict[str, tuple[str, ...]] = {
    "postgres": ("postgresql://", "postgres://"),
    "mysql": ("mysql://",),
    "clickhouse": ("clickhouse://", "clickhouses://", "http://", "https://"),
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_row_limit(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_ROW_LIMIT
    return max(1, min(n, MAX_ROW_LIMIT))


def _clamp_timeout(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(n, MAX_TIMEOUT_SECONDS))


def kind_matches_dsn(kind: str, dsn: str) -> bool:
    """Whether ``dsn``'s scheme looks like the right one for ``kind``.

    sqlite has no fixed scheme (a bare file path is the common case), so any
    non-empty value is accepted for it.
    """
    dsn = (dsn or "").strip()
    if not dsn:
        return False
    if kind == "sqlite":
        return True
    prefixes = _SCHEME_PREFIXES.get(kind)
    if not prefixes:
        return False
    return dsn.lower().startswith(prefixes)


def dsn_hint(dsn: str, kind: str) -> str:
    """The scheme and host only, never credentials: what ``public`` shows
    instead of the real dsn."""
    dsn = (dsn or "").strip()
    if not dsn:
        return ""
    if kind == "sqlite":
        path = dsn
        if path.startswith("sqlite://"):
            path = path[len("sqlite://"):]
        elif path.startswith("file:"):
            path = path[len("file:"):].split("?")[0]
        return f"sqlite://{Path(path).name}"
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return f"{kind}://<unparseable>"
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or ""
    return f"{parts.scheme}://{host}{port}{path}"


def public(connection: dict[str, Any]) -> dict[str, Any]:
    """A connection shaped for the UI or a tool result: the dsn replaced by
    ``dsn_hint``, everything else unchanged."""
    out = dict(connection)
    dsn = out.pop("dsn", "")
    out["dsn_hint"] = dsn_hint(dsn, str(connection.get("kind") or ""))
    return out


def _all_connections() -> list[dict[str, Any]]:
    items = STORE.get_cursor("connections", [])
    return [dict(c) for c in items] if isinstance(items, list) else []


def _save_connections(items: list[dict[str, Any]]) -> None:
    STORE.set_cursor("connections", items)


def list_connections(workspace: Optional[str] = None) -> list[dict[str, Any]]:
    items = _all_connections()
    if workspace is None:
        return items
    return [c for c in items if c.get("workspace") == workspace]


def get_connection(connection_id: str) -> Optional[dict[str, Any]]:
    for c in _all_connections():
        if c.get("id") == connection_id:
            return c
    return None


def find_connection(workspace: Optional[str], ref: str) -> Optional[dict[str, Any]]:
    """A connection by id, or by an unambiguous name within ``workspace``.

    Used by the tools (tools/databases.py), where an agent is more likely to
    have the connection's name in hand than its id.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None
    by_id = get_connection(ref)
    if by_id is not None and (not workspace or by_id.get("workspace") == workspace):
        return by_id
    matches = [c for c in list_connections(workspace) if c.get("name") == ref]
    return matches[0] if len(matches) == 1 else None


def create_connection(
    *, workspace: str, name: str, kind: str, dsn: str,
    allowed_schemas: Optional[Iterable[str]] = None,
    row_limit: Optional[int] = None, timeout_seconds: Optional[int] = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "workspace": workspace,
        "name": name,
        "kind": kind,
        "dsn": dsn,
        "allowed_schemas": [str(s) for s in allowed_schemas] if allowed_schemas else [],
        "row_limit": _clamp_row_limit(row_limit if row_limit is not None else DEFAULT_ROW_LIMIT),
        "timeout_seconds": _clamp_timeout(
            timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
        ),
        "created_at": _utc_iso(),
    }
    items = _all_connections()
    items.append(record)
    _save_connections(items)
    return dict(record)


def update_connection(connection_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    """Merge ``fields`` into the connection named by id; ``None`` values leave
    the existing field untouched (the same "no value supplied" convention the
    route's partial-update payload uses)."""
    items = _all_connections()
    updated: Optional[dict[str, Any]] = None
    for c in items:
        if c.get("id") != connection_id:
            continue
        if fields.get("name") is not None:
            c["name"] = fields["name"]
        if fields.get("kind") is not None:
            c["kind"] = fields["kind"]
        if fields.get("dsn") is not None:
            c["dsn"] = fields["dsn"]
        if fields.get("allowed_schemas") is not None:
            c["allowed_schemas"] = [str(s) for s in fields["allowed_schemas"]]
        if fields.get("row_limit") is not None:
            c["row_limit"] = _clamp_row_limit(fields["row_limit"])
        if fields.get("timeout_seconds") is not None:
            c["timeout_seconds"] = _clamp_timeout(fields["timeout_seconds"])
        updated = dict(c)
        break
    if updated is not None:
        _save_connections(items)
    return updated


def delete_connection(connection_id: str) -> bool:
    items = _all_connections()
    before = len(items)
    items = [c for c in items if c.get("id") != connection_id]
    if len(items) == before:
        return False
    _save_connections(items)
    return True


__all__ = [
    "KINDS", "DEFAULT_ROW_LIMIT", "MAX_ROW_LIMIT", "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS", "STORE", "kind_matches_dsn", "dsn_hint", "public",
    "list_connections", "get_connection", "find_connection", "create_connection",
    "update_connection", "delete_connection",
]
