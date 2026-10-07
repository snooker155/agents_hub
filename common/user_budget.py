"""
A spend limit per person (docs/costs.md "Limit per person").

In ``multi`` mode every account has a monthly money limit: its own
(``users.spend_limit_usd``, set by an administrator on the Users page) or,
when that is not set, the hub-wide default (``AGENTS_HUB_USER_SPEND_LIMIT_USD``,
also set on the Users page). ``0`` means unlimited, and so does a default of
``0``, which is where a fresh install starts.

A person's spend is what the accounting report (routes/accounting.py) shows on
their row: every run stamped ``launched_by`` them this UTC month (evaluation
channels excluded, the same leaf runs the report sums) plus their served
``/v1`` completions. :func:`check_user_budget` compares it against the limit
before a run starts and before each chat turn; ``common.budget.check_budget``
calls it, so every surface that honours a workspace's hard cap honours the
person's limit too. A breach raises :class:`UserBudgetExceededError`, a
:class:`~common.budget.BudgetExceededError`, so callers answer it the same way.

Nothing here applies outside ``multi`` (one operator, nobody to limit), or to
a run with no person behind it (the local operator, the service credential):
those are the workspace budgets' job.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from common.budget import BudgetExceededError

log = logging.getLogger(__name__)

PERIOD = "monthly"

#: Environment variable of the hub-wide default limit.
DEFAULT_LIMIT_ENV = "AGENTS_HUB_USER_SPEND_LIMIT_USD"


class UserBudgetExceededError(BudgetExceededError):
    """A person has spent their monthly limit."""

    def __init__(self, user_id: str, spend: float, limit: float, username: str = ""):
        self.user_id = user_id
        self.workspace = None
        self.spend = spend
        self.limit = limit
        self.period = PERIOD
        Exception.__init__(
            self,
            f"{'User ' + repr(username) if username else 'This account'} has reached "
            f"their monthly spend limit (${spend:.2f} spent of ${limit:.2f}). "
            "An administrator can raise it on the Users page. Run not started.",
        )


def _multi() -> bool:
    from common import identity
    from common.auth import MULTI
    return identity.current_mode() == MULTI


def _is_person(user_id: Optional[str]) -> bool:
    from common.auth import LOCAL_OPERATOR_ID
    from common.identity import SERVICE_PRINCIPAL
    return bool(user_id) and user_id not in (LOCAL_OPERATOR_ID, SERVICE_PRINCIPAL.id)


def default_limit() -> float:
    """The hub-wide default limit in USD per month, ``0`` for none."""
    import os
    from common.config import settings
    raw = os.environ.get(DEFAULT_LIMIT_ENV)
    if raw is None:
        raw = getattr(settings, "user_spend_limit_usd", 0.0)
    try:
        return max(0.0, float(raw or 0.0))
    except (TypeError, ValueError):
        return 0.0


def user_limit(user_id: str) -> Dict[str, Any]:
    """``{"limit_usd", "source"}``: the person's own limit (``source`` =
    ``"user"``) or the hub default (``"default"``). ``limit_usd`` 0 means
    unlimited."""
    from common import identity
    user = identity.get_user(user_id) or {}
    own = user.get("spend_limit_usd")
    if own is not None:
        return {"limit_usd": max(0.0, float(own)), "source": "user"}
    return {"limit_usd": default_limit(), "source": "default"}


def _month_start(now: Optional[datetime] = None) -> str:
    when = now or datetime.now(timezone.utc)
    return when.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def user_month_spend_usd(user_id: str, *, now: Optional[datetime] = None) -> float:
    """What ``user_id`` spent so far this UTC month: runs they launched plus
    their served ``/v1`` calls, priced as the accounting report prices them."""
    from common import db
    from common.pricing import EVALUATION_CHANNELS, container_cost_usd, load_price_map, run_cost_usd
    from managers.runs.store import _row_to_record

    since = _month_start(now)
    conn = db.get_conn()
    serving_row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM serving_usage WHERE user_id = ? AND at >= ?",
        (str(user_id), since)).fetchone()
    total = float((serving_row["c"] if serving_row else 0) or 0)

    prices = load_price_map()
    rows = conn.execute(
        f"SELECT * FROM runs WHERE {db.json_text('extra', 'launched_by')} = ? "
        "AND COALESCE(started_at, created_at) >= ?",
        (str(user_id), since)).fetchall()
    for row in rows:
        rec = _row_to_record(row)
        if (rec.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        reported = rec.get("reported_cost_usd")
        if isinstance(reported, (int, float)) and not isinstance(reported, bool):
            total += float(reported) + container_cost_usd(rec)
        else:
            total += run_cost_usd(rec, prices)
    return round(total, 6)


def user_budget_status(user_id: str) -> Dict[str, Any]:
    """Limit, its source, this month's spend and whether it is used up."""
    limit = user_limit(user_id)
    spend = user_month_spend_usd(user_id)
    cap = limit["limit_usd"]
    return {
        "user_id": user_id,
        "period": PERIOD,
        "limit_usd": cap,
        "source": limit["source"],
        "default_limit_usd": default_limit(),
        "spend": spend,
        "enforced": bool(cap),
        "exceeded": bool(cap) and spend >= cap,
    }


def check_user_budget(user_id: Optional[str] = None) -> None:
    """Raise :class:`UserBudgetExceededError` when the person a run is
    charged to (``common.attribution.launching_user`` unless given) has a
    limit and has reached it. A no-op outside ``multi`` and for runs with no
    person behind them. Fails open on a lookup error, like the workspace
    budget without ``fail_closed``."""
    if not _multi():
        return
    if user_id is None:
        from common.attribution import launching_user
        user_id = launching_user()
    if not _is_person(user_id):
        return
    try:
        cap = user_limit(user_id)["limit_usd"]
        if not cap:
            return
        spend = user_month_spend_usd(user_id)
    except Exception:  # noqa: BLE001 - fails open (see docstring)
        log.warning("user budget check failed for %s", user_id, exc_info=True)
        return
    if spend >= cap:
        from common import identity
        username = (identity.get_user(user_id) or {}).get("username") or ""
        raise UserBudgetExceededError(user_id, spend, cap, username)


__all__ = [
    "PERIOD", "DEFAULT_LIMIT_ENV", "UserBudgetExceededError", "default_limit", "user_limit",
    "user_month_spend_usd", "user_budget_status", "check_user_budget",
]
