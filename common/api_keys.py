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
from contextvars import ContextVar
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

#: Set for the duration of one request or relayed turn that presented a
#: personal key, read by whatever creates a run so it can stamp ``key_id`` on
#: the record (docs/costs.md "Attribution"). Mirrors
#: ``common.identity``'s ``_current_user``: a contextvar rather than a
#: parameter so the launchers deep under a request (agents.agent_launcher,
#: runtime.entity_launch, managers.runs.store) need no signature change, and
#: an asyncio task started from within the bound scope (widgets.relay.TurnRelay)
#: inherits its own copy. None for every other credential (a session, the
#: shared token, the service credential) and for anything with no request in
#: flight (a background job, a worker with no caller).
_current_key: ContextVar[Optional[str]] = ContextVar("agents_hub_current_key", default=None)


class KeyBudgetExceededError(Exception):
    """Raised when a personal key's money quota (``budget_usd_per_month``)
    would be exceeded by the request or run launch it is about to pay for."""

    def __init__(self, key_id: str, spend: float, limit: float):
        self.key_id = key_id
        self.spend = spend
        self.limit = limit
        super().__init__(
            f"API key has reached its monthly budget (${spend:.2f} spent of ${limit:.2f})")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current_key_id() -> Optional[str]:
    """The personal key bound to this context, or None."""
    return _current_key.get()


def set_current_key_id(key_id: Optional[str]):
    """Bind the current key for this context; returns the reset token."""
    return _current_key.set(key_id or None)


def reset_current_key_id(token) -> None:
    _current_key.reset(token)


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
        # Its own limits (migration 0017); None follows the hub-wide setting.
        "rate_limit_per_minute": _opt_int(row, "rate_limit_per_minute"),
        "tokens_per_day": _opt_int(row, "tokens_per_day"),
        # Its own money cap (migration 0025); NULL or 0 means none.
        "budget_usd_per_month": _opt_float(row, "budget_usd_per_month"),
    }


def _opt_int(row, column: str) -> Optional[int]:
    try:
        value = row[column]
    except (IndexError, KeyError):
        # A database not yet migrated to 0017: no per-key limit.
        return None
    return None if value is None else int(value)


def _opt_float(row, column: str) -> Optional[float]:
    try:
        value = row[column]
    except (IndexError, KeyError):
        # A database not yet migrated to 0025: no per-key budget.
        return None
    return None if value is None else float(value)


def _limit(value: Optional[int], name: str) -> Optional[int]:
    if value is None:
        return None
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} must be zero or positive")
    return value


def _money_limit(value: Optional[float], name: str = "budget_usd_per_month") -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    if value < 0:
        raise ValueError(f"{name} must be zero or positive")
    return value


def create_key(user_id: str, *, name: str = "", workspaces: Optional[List[str]] = None,
               expires_in_days: Optional[int] = None,
               rate_limit_per_minute: Optional[int] = None,
               tokens_per_day: Optional[int] = None,
               budget_usd_per_month: Optional[float] = None) -> Tuple[str, Dict[str, Any]]:
    """Cut a key. Returns ``(the key, its record)``: the key is never
    retrievable again. ``rate_limit_per_minute`` and ``tokens_per_day``
    override the hub-wide limits for this key (0 is unlimited, None follows
    the setting; common/rate_limit.py). ``budget_usd_per_month`` is the key's
    own money cap (0 or None means none; common.rate_limit.check_key_budget)."""
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
    per_minute = _limit(rate_limit_per_minute, "rate_limit_per_minute")
    per_day = _limit(tokens_per_day, "tokens_per_day")
    budget = _money_limit(budget_usd_per_month)
    key = KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
    key_id = secrets.token_hex(8)
    now = _now().isoformat()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO api_keys (key_id, key_hash, key_hint, user_id, name, workspaces, "
            "expires_at, created_at, last_used_at, revoked_at, rate_limit_per_minute, "
            "tokens_per_day, budget_usd_per_month) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
            (key_id, _hash(key), key[-4:], str(user_id), (name or "").strip()[:120],
             json.dumps(scope) if scope is not None else None, expires_at, now,
             per_minute, per_day, budget))
    return key, get_key(key_id)  # type: ignore[return-value]


#: A field left out of :func:`update_key_limits` entirely. Distinct from
#: ``None``, which clears the field back to "follow the hub-wide setting".
_UNSET = object()


def update_key_limits(key_id: str, *, user_id: Optional[str] = None,
                      rate_limit_per_minute: Any = _UNSET,
                      tokens_per_day: Any = _UNSET,
                      budget_usd_per_month: Any = _UNSET) -> Optional[Dict[str, Any]]:
    """Change a live key's own limits (its budget included). Only the fields
    actually passed change; a field left out (:data:`_UNSET`) is untouched,
    where an explicit ``None`` clears it back to the hub-wide setting.
    ``user_id`` restricts the match to that owner, as :func:`revoke_key` does,
    so a route can let people edit their own key without checking twice.
    Returns the fresh record, or None when there is no such (live) key."""
    sets: List[str] = []
    args: List[Any] = []
    if rate_limit_per_minute is not _UNSET:
        sets.append("rate_limit_per_minute = ?")
        args.append(_limit(rate_limit_per_minute, "rate_limit_per_minute"))
    if tokens_per_day is not _UNSET:
        sets.append("tokens_per_day = ?")
        args.append(_limit(tokens_per_day, "tokens_per_day"))
    if budget_usd_per_month is not _UNSET:
        sets.append("budget_usd_per_month = ?")
        args.append(_money_limit(budget_usd_per_month))

    where = "key_id = ? AND revoked_at IS NULL"
    args_full = args + [str(key_id)]
    if user_id:
        where += " AND user_id = ?"
        args_full.append(str(user_id))

    if not sets:
        # Nothing to change, but still only for a key this owner check would
        # have matched — an empty request must not become a way to read
        # someone else's key by id.
        row = db.get_conn().execute(f"SELECT key_id FROM api_keys WHERE {where}", args_full).fetchone()
        return get_key(key_id) if row is not None else None

    with db.transaction() as conn:
        cursor = conn.execute(f"UPDATE api_keys SET {', '.join(sets)} WHERE {where}", args_full)
    return get_key(key_id) if cursor.rowcount else None


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


def _utc_month_start(now: Optional[datetime] = None) -> str:
    when = now or _now()
    return when.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def key_month_spend_usd(key_id: str, *, now: Optional[datetime] = None) -> float:
    """A key's spend so far in the current UTC month: its own ``/v1`` serving
    cost plus the cost of every run charged to it, the same leaf runs the
    report sums (routes/accounting.py). A run is charged to a key when it is
    stamped with ``key_id`` at creation (common/attribution.py): one the
    request launched, and every run created inside a launched process, which
    inherits the key through its environment. So a flow's or a team's own
    runs are counted one by one, and the entity run's ``total_cost`` (their
    sum) is not added on top. What ``common.rate_limit.check_key_budget`` and
    the launch check compare against ``budget_usd_per_month``.
    """
    month_start = _utc_month_start(now)
    conn = db.get_conn()
    serving_row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM serving_usage WHERE key_id = ? AND at >= ?",
        (str(key_id), month_start)).fetchone()
    total = float((serving_row["c"] if serving_row else 0) or 0)

    from common.pricing import EVALUATION_CHANNELS, load_price_map, run_cost_usd
    from managers.runs.store import _row_to_record
    prices = load_price_map()
    run_rows = conn.execute(
        f"SELECT * FROM runs WHERE {db.json_text('extra', 'key_id')} = ? "
        "AND COALESCE(started_at, created_at) >= ?",
        (str(key_id), month_start)).fetchall()
    for row in run_rows:
        rec = _row_to_record(row)
        if (rec.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        reported = rec.get("reported_cost_usd")
        if isinstance(reported, (int, float)) and not isinstance(reported, bool):
            total += float(reported)
        else:
            total += run_cost_usd(rec, prices)

    return round(total, 6)


__all__ = [
    "KEY_PREFIX", "KeyBudgetExceededError", "TOUCH_INTERVAL", "create_key",
    "current_key_id", "get_key", "key_month_spend_usd", "list_keys",
    "looks_like_key", "reset_current_key_id", "resolve", "revoke_all",
    "revoke_key", "set_current_key_id", "update_key_limits",
]
