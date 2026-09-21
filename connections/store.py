"""The connection record and its token.

File-backed, like the agent registry it sits beside: a hub has a handful of
connections, they change rarely, and a JSON file keeps them readable and
hand-editable when something has to be fixed without the UI. Reads are cached
on the file's mtime, because every ingest request authenticates against this
file and a production graph reports often.

**What the token is for.** It tells the hub which process is reporting. This is
a single-tenant install with no user accounts and no authorisation model, so the
token is an identity for a machine, not a permission granted to a person: it
says "these runs are the billing graph's", and it means the hub can stop
listening to one reporter (rotate, disable, delete) without touching the others.

It is still handled as a secret, because a value that can speak for a connection
should not be lying around in plain text:

* generated once and never stored — only its SHA-256 hash is, so a copied
  ``connections.json`` cannot be used to report;
* compared in constant time, so the file cannot be probed by timing;
* rotating or deleting one record affects only that one reporter.

SHA-256 rather than a slow KDF on purpose: these are 256-bit random secrets,
not passwords, so there is no dictionary to make expensive, and the hash is on
the hot path of every ingest call.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from filelock import FileLock

from common.paths import AGENTS_HUB_ROOT

CONNECTIONS_FILE = AGENTS_HUB_ROOT / "connections.json"
CONNECTIONS_LOCK = AGENTS_HUB_ROOT / "connections.json.lock"

# Prefix on every issued token. Makes one recognisable in a log or a config
# file, and lets a secret scanner match it.
TOKEN_PREFIX = "ahc_"

# The kinds a connection can declare. Free text would make the UI's badges
# meaningless, and the list is cheap to extend when something new shows up.
KINDS = ("langgraph", "crewai", "autogen", "llamaindex", "http", "other")

_lock = threading.Lock()
_cache: Optional[List[Dict[str, Any]]] = None
_cache_stamp: Optional[tuple] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Connection:
    """Something outside that reports runs into this hub."""

    id: str
    name: str
    kind: str = "other"
    # Only "observe" today. The field exists because the same record will carry
    # a connection the hub may also call, and a mode that is implied rather than
    # stored is the kind of thing that later needs a migration.
    mode: str = "observe"
    description: str = ""
    workspace: Optional[str] = None
    token_hash: str = ""
    # Last few characters of the token, so the UI can tell two apart without
    # holding either.
    token_hint: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    last_seen: Optional[str] = None
    disabled: bool = False
    # The reported shape of the graph, same format as an imported agent's
    # (agents/remote_agent.normalize_topology).
    topology: Dict[str, Any] = field(default_factory=dict)
    # How many runs to keep for this connection, newest first. None means "use
    # the hub's default" (``settings.connection_retention_runs``); 0 means keep
    # everything, which is a choice an operator can make for a quiet connection
    # and should not be made for them.
    retention_runs: Optional[int] = None

    def to_dict(self, *, include_hash: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_hash:
            data.pop("token_hash", None)
        return data


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _read() -> List[Dict[str, Any]]:
    """Every record, from cache when the file has not changed."""
    global _cache, _cache_stamp
    try:
        stat = CONNECTIONS_FILE.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        with _lock:
            _cache, _cache_stamp = [], None
        return []

    with _lock:
        if _cache is not None and _cache_stamp == stamp:
            return list(_cache)

    try:
        raw = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt file must not take the API down: an empty list means "no
        # connection authenticates", which fails closed.
        raw = []
    records = [r for r in raw if isinstance(r, dict) and r.get("id")]

    with _lock:
        _cache, _cache_stamp = records, stamp
    return list(records)


def _write(records: List[Dict[str, Any]]) -> None:
    global _cache, _cache_stamp
    CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(CONNECTIONS_LOCK), timeout=10):
        CONNECTIONS_FILE.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        _cache, _cache_stamp = None, None


def _mutate(connection_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply changes to one record under the file lock. Returns the new record.

    Read-modify-write inside the lock rather than around ``_read``: two ingest
    workers touching ``last_seen`` at the same moment would otherwise drop one
    another's writes, and with them whatever else was being changed.
    """
    global _cache, _cache_stamp
    CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(CONNECTIONS_LOCK), timeout=10):
        try:
            raw = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = []
        records = [r for r in raw if isinstance(r, dict) and r.get("id")]
        updated = None
        for i, record in enumerate(records):
            if record.get("id") == connection_id:
                updated = {**record, **changes}
                records[i] = updated
                break
        if updated is None:
            return None
        CONNECTIONS_FILE.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        _cache, _cache_stamp = None, None
    return updated


# ── reads ────────────────────────────────────────────────────────────────────

def visible_in_workspace(record: Dict[str, Any], workspace: Optional[str]) -> bool:
    """Whether this connection belongs to the workspace being looked at.

    A connection lives in one workspace, like everything else here. Created
    without one, it lives in ``default`` — not everywhere: a connection is a
    thing somebody attached, and it belongs where it was attached rather than
    following the reader around.

    Asking for no workspace at all means "every workspace", which is what the
    hub-wide views and the API without a ``?workspace=`` want.

    This is about where things are filed, not about who may touch them. The hub
    is a single-tenant install with no accounts, and the workspace is a place,
    not a permission. See docs/connections.md.
    """
    if not workspace:
        return True
    owner = record.get("workspace") or "default"
    return owner == workspace


def list_connections(workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every connection, or only one workspace's, without token hashes."""
    records = [r for r in _read() if visible_in_workspace(r, workspace)]
    return [Connection(**_coerce(r)).to_dict() for r in records]


def get_connection(connection_id: str, workspace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One connection, or None when it does not exist *here*.

    A connection from another workspace reads as absent rather than refused,
    which is how every other workspace-scoped thing in this product behaves: a
    page shows what is in the workspace it is looking at.
    """
    for record in _read():
        if record.get("id") == connection_id:
            if not visible_in_workspace(record, workspace):
                return None
            return Connection(**_coerce(record)).to_dict()
    return None


def resolve_token(token: str) -> Optional[Dict[str, Any]]:
    """The connection a token authenticates, or None.

    Compared in constant time against every record, and the loop is not cut
    short on a match, so a caller cannot learn where in the file a token sits
    from how long the answer took.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    presented = _hash(token)
    found: Optional[Dict[str, Any]] = None
    for record in _read():
        if secrets.compare_digest(str(record.get("token_hash") or ""), presented):
            found = record
    if found is None or found.get("disabled"):
        return None
    return Connection(**_coerce(found)).to_dict()


def _coerce(record: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only fields the dataclass knows, so an older or newer file loads."""
    allowed = set(Connection.__dataclass_fields__)
    return {k: v for k, v in record.items() if k in allowed}


# ── writes ───────────────────────────────────────────────────────────────────

def _new_token() -> Tuple[str, str, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, _hash(token), token[-4:]


def create_connection(
    *,
    connection_id: str,
    name: str = "",
    kind: str = "other",
    description: str = "",
    workspace: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """Create a connection and issue its token.

    Returns the record and the token. The token is returned exactly once, here:
    nothing stores it, so a lost token is rotated rather than looked up.
    """
    connection_id = (connection_id or "").strip()
    if not connection_id:
        raise ValueError("a connection needs an id")
    if get_connection(connection_id) is not None:
        raise ValueError(f"connection '{connection_id}' already exists")

    token, token_hash, hint = _new_token()
    record = Connection(
        id=connection_id,
        name=(name or "").strip() or connection_id,
        kind=kind if kind in KINDS else "other",
        description=description or "",
        workspace=(workspace or "").strip() or None,
        token_hash=token_hash,
        token_hint=hint,
    )
    records = _read()
    records.append(asdict(record))
    _write(records)
    return record.to_dict(), token


def rotate_token(connection_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """Issue a new token and invalidate the old one immediately."""
    token, token_hash, hint = _new_token()
    updated = _mutate(connection_id, {"token_hash": token_hash, "token_hint": hint})
    if updated is None:
        return None
    return Connection(**_coerce(updated)).to_dict(), token


def update_connection(connection_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Edit the descriptive fields. The token is never touched from here."""
    editable = {"name", "kind", "description", "workspace", "disabled", "retention_runs"}
    safe = {k: v for k, v in (changes or {}).items() if k in editable}
    if "kind" in safe and safe["kind"] not in KINDS:
        safe["kind"] = "other"
    if "retention_runs" in safe and safe["retention_runs"] is not None:
        try:
            safe["retention_runs"] = max(0, int(safe["retention_runs"]))
        except (TypeError, ValueError):
            safe.pop("retention_runs")
    if not safe:
        return get_connection(connection_id)
    updated = _mutate(connection_id, safe)
    return Connection(**_coerce(updated)).to_dict() if updated else None


def set_topology(connection_id: str, topology: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Store the shape the connection reported for itself."""
    updated = _mutate(connection_id, {"topology": topology or {}})
    return Connection(**_coerce(updated)).to_dict() if updated else None


def touch(connection_id: str) -> None:
    """Record that this connection was heard from. Best effort by design.

    A failed heartbeat must never fail the ingest call it was attached to: the
    run being reported matters, the freshness stamp does not.
    """
    try:
        _mutate(connection_id, {"last_seen": _utc_now_iso()})
    except Exception:
        pass


def delete_connection(connection_id: str) -> bool:
    """Remove a connection. Its token stops working immediately.

    Runs already recorded are left alone: they are the history of what happened,
    and deleting the connection that reported them would erase the record of
    work that really ran.
    """
    records = _read()
    remaining = [r for r in records if r.get("id") != connection_id]
    if len(remaining) == len(records):
        return False
    _write(remaining)
    return True


__all__ = [
    "Connection", "CONNECTIONS_FILE", "KINDS", "TOKEN_PREFIX", "visible_in_workspace",
    "create_connection", "delete_connection", "get_connection", "list_connections",
    "resolve_token", "rotate_token", "set_topology", "touch", "update_connection",
]
