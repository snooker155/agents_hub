"""
The consent portal's rows: requests (``consent_requests``) and each agent's
settings (``consent_settings``), both from common/migrations/0039_consent_requests.py.

A request's life is one short round trip (pending, started, then granted,
denied, failed or expired) and, for a grant, a longer tail until somebody
revokes it. The grant itself (the refresh token) is never here: it is a
personal secret (common/secrets.py), and this table only says which request
produced it and for which account, so the operator can list and revoke grants
without decrypting anything.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from common import db

from . import catalog

#: Statuses a link may still be used in.
OPEN_STATUSES = ("pending", "started")

_COLUMNS = ("request_id", "workspace", "agent_id", "principal", "provider", "scopes", "purpose",
            "status", "state_hash", "pkce_verifier", "account_email", "error", "run_id",
            "created_at", "expires_at", "completed_at", "revoked_at", "revoked_by")

#: The longest purpose an agent may put on the page.
PURPOSE_MAX = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _row(row) -> Dict[str, Any]:
    out = {c: row[c] for c in _COLUMNS}
    out["scopes"] = [s for s in str(out.get("scopes") or "").split() if s]
    return out


def _select(where: str, params: tuple, *, order: str = "created_at DESC",
            limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = f"SELECT {', '.join(_COLUMNS)} FROM consent_requests WHERE {where} ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_row(r) for r in db.get_conn().execute(sql, params).fetchall()]


# ── requests ─────────────────────────────────────────────────────────────────

def create_request(*, workspace: str, agent_id: str, principal: str, provider: str,
                   scopes: List[str], purpose: str = "", ttl_seconds: int,
                   run_id: Optional[str] = None) -> Dict[str, Any]:
    now = _now()
    request_id = uuid.uuid4().hex
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO consent_requests (request_id, workspace, agent_id, principal, provider, "
            "scopes, purpose, status, run_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (request_id, str(workspace), str(agent_id), str(principal), str(provider),
             " ".join(scopes), str(purpose or "")[:PURPOSE_MAX], run_id or None,
             _iso(now), _iso(now + timedelta(seconds=int(ttl_seconds)))))
    return get_request(request_id) or {}


def get_request(request_id: str) -> Optional[Dict[str, Any]]:
    rows = _select("request_id = ?", (str(request_id),), limit=1)
    return rows[0] if rows else None


def by_state_hash(state_hash: str) -> Optional[Dict[str, Any]]:
    if not state_hash:
        return None
    rows = _select("state_hash = ? AND status = 'started'", (str(state_hash),), limit=1)
    return rows[0] if rows else None


def is_expired(row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    expires = _parse(row.get("expires_at"))
    return expires is None or expires <= (now or _now())


def update(request_id: str, **fields: Any) -> None:
    allowed = {k: v for k, v in fields.items() if k in _COLUMNS and k != "request_id"}
    if not allowed:
        return
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with db.transaction() as conn:
        conn.execute(f"UPDATE consent_requests SET {sets} WHERE request_id = ?",
                     (*allowed.values(), str(request_id)))


def start(request_id: str, *, state_hash: str, pkce_verifier: str) -> bool:
    """Move an open request to ``started`` with a fresh state. False when it
    is no longer open (used, expired, revoked) so the caller refuses."""
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE consent_requests SET status = 'started', state_hash = ?, pkce_verifier = ? "
            "WHERE request_id = ? AND status IN ('pending', 'started')",
            (state_hash, pkce_verifier, str(request_id)))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def finish(request_id: str, status: str, *, account_email: Optional[str] = None,
           error: Optional[str] = None) -> bool:
    """Close the round trip: the state and the verifier are gone with it, so
    the same callback cannot be replayed. False when another callback closed
    it first (the row is single use)."""
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE consent_requests SET status = ?, state_hash = NULL, pkce_verifier = NULL, "
            "account_email = COALESCE(?, account_email), error = ?, completed_at = ? "
            "WHERE request_id = ? AND status IN ('pending', 'started')",
            (status, account_email, (error or None) and str(error)[:500], _iso(_now()),
             str(request_id)))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def expire_open(now: Optional[datetime] = None) -> int:
    """Mark open requests past their time ``expired``. Returns how many."""
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE consent_requests SET status = 'expired', state_hash = NULL, "
            "pkce_verifier = NULL, completed_at = ? "
            "WHERE status IN ('pending', 'started') AND expires_at <= ?",
            (_iso(now or _now()), _iso(now or _now())))
        return int(getattr(cur, "rowcount", 0) or 0)


def recent_open_count(principal: str, *, since_seconds: int = 3600) -> int:
    """Links handed out to one end user lately, for the tool's own limit."""
    since = _iso(_now() - timedelta(seconds=since_seconds))
    row = db.get_conn().execute(
        "SELECT COUNT(*) FROM consent_requests WHERE principal = ? AND created_at >= ?",
        (str(principal), since)).fetchone()
    return int(row[0] or 0)


def supersede(workspace: str, agent_id: str, principal: str, provider: str,
              keep_request_id: str) -> None:
    """A new grant replaces the previous one for the same end user."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE consent_requests SET status = 'replaced' WHERE workspace = ? AND agent_id = ? "
            "AND principal = ? AND provider = ? AND status = 'granted' AND request_id <> ?",
            (str(workspace), str(agent_id), str(principal), str(provider), str(keep_request_id)))


def mark_revoked(workspace: str, agent_id: str, principal: str, provider: str, *,
                 by: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE consent_requests SET status = 'revoked', revoked_at = ?, revoked_by = ? "
            "WHERE workspace = ? AND agent_id = ? AND principal = ? AND provider = ? "
            "AND status = 'granted'",
            (_iso(_now()), str(by)[:200], str(workspace), str(agent_id), str(principal),
             str(provider)))
        return int(getattr(cur, "rowcount", 0) or 0)


def grant_row(workspace: str, agent_id: str, principal: str,
              provider: str) -> Optional[Dict[str, Any]]:
    rows = _select("workspace = ? AND agent_id = ? AND principal = ? AND provider = ? "
                   "AND status = 'granted'",
                   (str(workspace), str(agent_id), str(principal), str(provider)),
                   order="completed_at DESC", limit=1)
    return rows[0] if rows else None


def list_grants(workspace: str, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Live grants in a workspace (optionally one agent's), newest first."""
    where = "workspace = ? AND status = 'granted'"
    params: List[Any] = [str(workspace)]
    if agent_id:
        where += " AND agent_id = ?"
        params.append(str(agent_id))
    return _select(where, tuple(params), order="completed_at DESC")


# ── settings ─────────────────────────────────────────────────────────────────

def get_settings(agent_id: str) -> Dict[str, Any]:
    """``{providers: [...], scopes: {provider: [keys]}}``; empty for an agent
    with no row. Unknown providers and keys left in a row are dropped."""
    row = db.get_conn().execute(
        "SELECT providers, scopes, updated_at FROM consent_settings WHERE agent_id = ?",
        (str(agent_id),)).fetchone()
    if row is None:
        return {"providers": [], "scopes": {}}
    try:
        providers = [p for p in json.loads(row["providers"] or "[]") if catalog.is_provider(p)]
        raw_scopes = json.loads(row["scopes"] or "{}")
    except (TypeError, ValueError):
        return {"providers": [], "scopes": {}}
    scopes: Dict[str, List[str]] = {}
    for provider in catalog.PROVIDERS:
        keys = raw_scopes.get(provider) if isinstance(raw_scopes, dict) else None
        offered = catalog.SCOPES[provider]
        scopes[provider] = [k for k in offered if k in (keys or [])]
    return {"providers": providers, "scopes": scopes, "updated_at": row["updated_at"]}


def save_settings(agent_id: str, providers: List[str], scopes: Dict[str, List[str]], *,
                  by: Optional[str] = None) -> Dict[str, Any]:
    """Validate and store. Raises ValueError for an unknown provider or key."""
    clean_providers: List[str] = []
    for p in providers or []:
        name = str(p or "").strip().lower()
        if not catalog.is_provider(name):
            raise ValueError(f"unknown provider '{p}'")
        if name not in clean_providers:
            clean_providers.append(name)
    clean_scopes = {p: catalog.clean_keys(p, (scopes or {}).get(p) or []) for p in catalog.PROVIDERS}
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql("consent_settings", ("agent_id", "providers", "scopes", "updated_at",
                                               "updated_by"), ("agent_id",)),
            (str(agent_id), json.dumps(clean_providers), json.dumps(clean_scopes),
             _iso(_now()), by or ""))
    return get_settings(agent_id)


def settings_signature(agent_id: str) -> str:
    """A cheap change marker for one agent's settings (agents/agent_cache.py
    rebuilds an agent whose consent tools come or go)."""
    try:
        row = db.get_conn().execute(
            "SELECT updated_at, providers FROM consent_settings WHERE agent_id = ?",
            (str(agent_id),)).fetchone()
    except Exception:  # noqa: BLE001 - no table yet (an old database): nothing to fingerprint
        return ""
    return f"{row['updated_at']}:{row['providers']}" if row else ""


__all__ = [
    "OPEN_STATUSES", "PURPOSE_MAX", "by_state_hash", "create_request", "expire_open", "finish",
    "get_request", "get_settings", "grant_row", "is_expired", "list_grants", "mark_revoked",
    "recent_open_count", "save_settings", "settings_signature", "start", "supersede", "update",
]
