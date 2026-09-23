"""
Personal API keys: a credential that acts as its owner, never wider.

Under ``AUTH_MODE=multi`` a browser has a session, and a subprocess the hub
launched has the service credential, but a CLI in REST mode, an A2A client
or a CI job has neither. A key is what they present instead of the shared
``AGENTS_HUB_API_TOKEN`` (which only ``token`` mode keeps).

A key is cut for one account, optionally narrowed to a list of workspaces,
optionally with an expiry. Presented, it resolves to the same principal its
owner would get from a login, with ``scope`` set to the narrowing: the
guard then refuses any workspace outside it before roles are even looked
at (``common.auth.authorize``), admin or not. The key stops working the
moment its owner is disabled or deleted, when it is revoked, and when it
expires.

Storage follows sessions and connection tokens: the key itself is shown once
and never stored; the row is keyed by its SHA-256 and keeps the last four
characters as a hint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from common import db

log = logging.getLogger(__name__)

#: Every key starts with this, so one is recognisable in a log or a config
#: file, and so that :func:`resolve` can skip the lookup for anything else.
KEY_PREFIX = "ahk_"
_KEY_BYTES = 32
#: ``last_used_at`` is refreshed at most this often, so a busy client does
#: not turn every read into a write.
TOUCH_INTERVAL = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(key: str) -> str:
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _row_to_key(row) -> Dict[str, Any]:
    try:
        workspaces = json.loads(row["workspaces"]) if row["workspaces"] else None
    except (TypeError, ValueError):
        workspaces = None
    return {
        "id": row["key_id"], "name": row["name"] or "", "hint": row["key_hint"] or "",
        "user_id": row["user_id"],
        "workspaces": list(workspaces) if isinstance(workspaces, list) else None,
        "expires_at": row["expires_at"], "created_at": row["created_at"],
        "last_used_at": row["last_used_at"], "revoked_at": row["revoked_at"],
    }


def create_key(user_id: str, *, name: str = "", workspaces: Optional[List[str]] = None,
               expires_in_days: Optional[int] = None) -> Tuple[str, Dict[str, Any]]:
    """Cut a key. Returns ``(the key, its record)``: the key is never
    retrievable again."""
    from common import identity
    if identity.get_user(user_id) is None:
        raise ValueError(f"no such user: {user_id}")
    scope: Optional[List[str]] = None
    if workspaces is not None:
        scope = sorted({str(w).strip() for w in workspaces if str(w).strip()})
        if not scope:
            scope = None
    expires_at = None
    if expires_in_days:
        days = int(expires_in_days)
        if days <= 0:
            raise ValueError("expires_in_days must be positive")
        expires_at = (_now() + timedelta(days=days)).isoformat()
    key = KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
    key_id = secrets.token_hex(8)
    now = _now().isoformat()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO api_keys (key_id, key_hash, key_hint, user_id, name, workspaces, "
            "expires_at, created_at, last_used_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (key_id, _hash(key), key[-4:], str(user_id), (name or "").strip()[:120],
             json.dumps(scope) if scope is not None else None, expires_at, now))
    return key, get_key(key_id)  # type: ignore[return-value]


def get_key(key_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM api_keys WHERE key_id = ?",
                                (str(key_id),)).fetchone()
    return _row_to_key(row) if row else None


def list_keys(user_id: Optional[str] = None, *, include_revoked: bool = False) -> List[Dict[str, Any]]:
    """One user's keys, or everyone's (admin), newest first."""
    where = []
    args: List[Any] = []
    if user_id:
        where.append("user_id = ?")
        args.append(str(user_id))
    if not include_revoked:
        where.append("revoked_at IS NULL")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.get_conn().execute(
        f"SELECT * FROM api_keys{clause} ORDER BY created_at DESC", tuple(args)).fetchall()
    return [_row_to_key(r) for r in rows]


def revoke_key(key_id: str, *, user_id: Optional[str] = None) -> bool:
    """Revoke one key. With ``user_id`` only that owner's key qualifies, so a
    route can let people revoke their own without checking twice."""
    with db.transaction() as conn:
        if user_id:
            cursor = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND user_id = ? "
                "AND revoked_at IS NULL", (_now().isoformat(), str(key_id), str(user_id)))
        else:
            cursor = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
                (_now().isoformat(), str(key_id)))
    return bool(cursor.rowcount)


def revoke_all(user_id: str) -> int:
    """Every live key of one user, on disable or delete."""
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now().isoformat(), str(user_id)))
    return int(cursor.rowcount or 0)


def looks_like_key(presented: Optional[str]) -> bool:
    return bool(presented) and str(presented).startswith(KEY_PREFIX)


def resolve(presented: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """The ``(user, key)`` a presented key authenticates, or None.

    None for an unknown, revoked or expired key and for a disabled owner
    alike. Touches ``last_used_at`` at most once a minute.
    """
    if not looks_like_key(presented):
        return None
    row = db.get_conn().execute(
        "SELECT k.*, u.disabled AS owner_disabled FROM api_keys k "
        "JOIN users u ON u.user_id = k.user_id WHERE k.key_hash = ?",
        (_hash(presented),)).fetchone()
    if row is None or row["revoked_at"] or row["owner_disabled"]:
        return None
    expires = _parse(row["expires_at"])
    if expires is not None and expires <= _now():
        return None
    from common import identity
    user = identity.get_user(row["user_id"])
    if user is None or user.get("disabled"):
        return None
    last = _parse(row["last_used_at"])
    now = _now()
    if last is None or now - last >= TOUCH_INTERVAL:
        try:
            with db.transaction() as conn:
                conn.execute("UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                             (now.isoformat(), row["key_id"]))
        except Exception:  # noqa: BLE001 - a last_used touch must not break key resolution
            log.debug("last_used_at update failed for key %s", row["key_id"], exc_info=True)
    return user, _row_to_key(row)


__all__ = [
    "KEY_PREFIX", "TOUCH_INTERVAL", "create_key", "get_key", "list_keys",
    "looks_like_key", "resolve", "revoke_all", "revoke_key",
]
