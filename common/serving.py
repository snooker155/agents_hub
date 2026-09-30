"""
Accounting for the hub as an OpenAI-compatible model server
(docs/hub-as-provider.md).

:func:`record_usage` writes one ``serving_usage`` row per completion call
(migration 0016) and :func:`usage` aggregates them per catalog model for the
Models page. Tokens only: the plan counts tokens without cost, so nothing
here reads a price.

Recording must never break the response it describes, the same rule the
audit log follows: :func:`record_usage` swallows every error and logs it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

log = logging.getLogger(__name__)

_IS_ERROR = "status = 'error'"

#: The rule used when a provider reports no usage at all.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Four characters per token, rounded up; at least one for any text."""
    text = text or ""
    if not text:
        return 0
    return max(1, -(-len(text) // CHARS_PER_TOKEN))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(principal: Any) -> Dict[str, Optional[str]]:
    if principal is None:
        return {"user_id": None, "actor_kind": "system", "actor_name": None, "key_id": None}
    via = getattr(principal, "via", "") or ""
    return {
        "user_id": getattr(principal, "id", None),
        "actor_kind": via or getattr(principal, "kind", None),
        "actor_name": getattr(principal, "username", None),
        # Only a personal key's id is worth keeping: a session id says nothing
        # a person could act on, and the key id is what they revoke.
        "key_id": (getattr(principal, "credential_id", "") or None) if via == "api_key" else None,
    }


def record_usage(principal: Any, *, provider: str, model: str,
                 prompt_tokens: int = 0, completion_tokens: int = 0,
                 duration_ms: int = 0, stream: bool = False, status: str = "ok",
                 error: Optional[str] = None, estimated: bool = False) -> Optional[int]:
    """Append one row. Returns its id, or None when writing failed. Never raises."""
    actor = _actor(principal)
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    try:
        from common.pricing import serving_cost_usd
        cost = serving_cost_usd(provider, model, prompt, completion)
    except Exception:  # noqa: BLE001 - an unpriceable call still gets recorded, at $0
        log.debug("serving: could not price %s/%s", provider, model, exc_info=True)
        cost = 0.0
    try:
        with db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO serving_usage (at, user_id, actor_kind, actor_name, key_id, "
                "provider, model, prompt_tokens, completion_tokens, total_tokens, "
                "duration_ms, stream, status, error, estimated, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (_now(), actor["user_id"], actor["actor_kind"], actor["actor_name"],
                 actor["key_id"], str(provider), str(model), prompt, completion,
                 prompt + completion, max(0, int(duration_ms or 0)), 1 if stream else 0,
                 "error" if status == "error" else "ok",
                 (str(error)[:2000] if error else None), 1 if estimated else 0, cost),
            )
            row = cursor.fetchone()
            return (row[0] if row else None) or None
    except Exception:  # noqa: BLE001 - accounting must not break the completion it describes
        log.warning("serving: could not record usage for %s/%s", provider, model, exc_info=True)
        return None


def _where(since: Optional[str], until: Optional[str],
           user_id: Optional[str]) -> tuple[str, list]:
    where: List[str] = []
    args: List[Any] = []
    if user_id:
        where.append("user_id = ?")
        args.append(str(user_id))
    if since:
        where.append("at >= ?")
        args.append(since)
    if until:
        where.append("at <= ?")
        args.append(until)
    return ((" WHERE " + " AND ".join(where)) if where else ""), args


def tokens_today(*, user_id: Optional[str] = None, key_id: Optional[str] = None,
                 now: Optional[datetime] = None) -> int:
    """Prompt plus completion tokens of the successful calls since 00:00 UTC,
    for one personal key or one user (``common.rate_limit``'s tokens per day).
    A failed call is not counted: it spent nothing the caller got back. Zero
    when neither is given."""
    if not user_id and not key_id:
        return 0
    now = now or datetime.now(timezone.utc)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    column, value = ("key_id", key_id) if key_id else ("user_id", user_id)
    row = db.get_conn().execute(
        "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS used "
        f"FROM serving_usage WHERE {column} = ? AND at >= ? AND status = 'ok'",
        (str(value), since)).fetchone()
    return int((row["used"] if row else 0) or 0)


def usage(since: Optional[str] = None, until: Optional[str] = None,
          limit_recent: int = 50, *, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Per-model totals, overall totals and the most recent calls.

    ``since`` and ``until`` are ISO timestamps compared as text, which is
    exact for the UTC ISO form every row is written with. ``user_id``
    narrows everything to one person's calls (what a non-administrator sees
    in ``multi`` mode).
    """
    clause, args = _where(since, until, user_id)
    conn = db.get_conn()
    grouped = conn.execute(
        "SELECT provider, model, COUNT(*) AS requests, "
        "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
        "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
        "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
        "COALESCE(SUM(cost_usd), 0) AS cost_usd, "
        f"{db.sum_if(_IS_ERROR)} AS errors, "
        "MAX(at) AS last_at "
        f"FROM serving_usage{clause} GROUP BY provider, model "
        "ORDER BY total_tokens DESC, requests DESC, provider, model",
        tuple(args)).fetchall()
    rows = [{
        "provider": r["provider"], "model": r["model"],
        "requests": int(r["requests"] or 0),
        "prompt_tokens": int(r["prompt_tokens"] or 0),
        "completion_tokens": int(r["completion_tokens"] or 0),
        "total_tokens": int(r["total_tokens"] or 0),
        "cost": round(float(r["cost_usd"] or 0.0), 4),
        "errors": int(r["errors"] or 0),
        "last_at": r["last_at"],
    } for r in grouped]
    totals = {
        "requests": sum(r["requests"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 4),
    }
    limit = max(0, min(int(limit_recent or 0), 1000))
    recent: List[Dict[str, Any]] = []
    if limit:
        latest = conn.execute(
            "SELECT at, actor_name, actor_kind, provider, model, prompt_tokens, "
            f"completion_tokens, duration_ms, stream, status, cost_usd FROM serving_usage{clause} "
            "ORDER BY id DESC LIMIT ?", tuple(args) + (limit,)).fetchall()
        recent = [{
            "at": r["at"], "actor_name": r["actor_name"], "actor_kind": r["actor_kind"],
            "provider": r["provider"], "model": r["model"],
            "prompt_tokens": int(r["prompt_tokens"] or 0),
            "completion_tokens": int(r["completion_tokens"] or 0),
            "duration_ms": int(r["duration_ms"] or 0),
            "stream": bool(r["stream"]), "status": r["status"],
            "cost": round(float(r["cost_usd"] or 0.0), 4),
        } for r in latest]
    return {"rows": rows, "totals": totals, "recent": recent}
