"""
Per-workspace cost budgets.

A workspace can carry a ``budget`` block in its metadata::

    {"hard_limit_usd": 0.0, "soft_limit_usd": 0.0, "period": "monthly"}

``period`` is one of ``total`` / ``daily`` / ``monthly`` (UTC). A limit of ``0``
means *disabled* — the default, so existing workspaces keep their current
behaviour (this feature is opt-in). ``hard_limit_usd`` is enforced at run launch:
when the period's estimated spend already meets or exceeds it, ``check_budget``
raises :class:`BudgetExceededError` and the run is not started. Everything here
fails open — any lookup/pricing error results in *no* enforcement rather than a
wedged workspace.

Spend is estimated from recorded runs (``run_manager.load_runs``) joined against
catalog pricing (``common.pricing``); it is the same number the Costs page shows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from common.pricing import EVALUATION_CHANNELS

VALID_PERIODS = ("total", "daily", "monthly")

_DEFAULT_BUDGET: Dict[str, Any] = {
    "hard_limit_usd": 0.0,
    "soft_limit_usd": 0.0,
    "period": "monthly",
}


class BudgetExceededError(Exception):
    """Raised when launching a run would violate a workspace's hard budget cap."""

    def __init__(self, workspace: str, spend: float, limit: float, period: str):
        self.workspace = workspace
        self.spend = spend
        self.limit = limit
        self.period = period
        super().__init__(
            f"Workspace '{workspace}' has reached its {period} budget cap "
            f"(${spend:.2f} spent of ${limit:.2f}). Run not started."
        )


def normalize_budget(raw: Any) -> Dict[str, Any]:
    """Coerce a stored/incoming budget block to the canonical schema."""
    src = raw if isinstance(raw, dict) else {}

    def _num(key: str) -> float:
        try:
            return max(0.0, float(src.get(key) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    period = str(src.get("period") or _DEFAULT_BUDGET["period"]).strip().lower()
    if period not in VALID_PERIODS:
        period = _DEFAULT_BUDGET["period"]
    return {
        "hard_limit_usd": _num("hard_limit_usd"),
        "soft_limit_usd": _num("soft_limit_usd"),
        "period": period,
    }


def period_start(period: str, now: Optional[datetime] = None) -> Optional[str]:
    """ISO (UTC) lower bound for a period, or ``None`` for ``total`` (all time)."""
    now = now or datetime.now(timezone.utc)
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return start.isoformat()


def get_budget(workspace: str) -> Dict[str, Any]:
    """Read a workspace's budget config, defaulted + normalized. Fails open to
    the disabled default on any metadata error."""
    try:
        from workspace import get_workspace_metadata

        meta = get_workspace_metadata(workspace) or {}
        return normalize_budget(meta.get("budget"))
    except Exception:
        return dict(_DEFAULT_BUDGET)


def set_budget(workspace: str, budget: Any) -> Dict[str, Any]:
    """Persist a workspace's budget config; returns the normalized value."""
    from workspace import create_workspace_folder, update_workspace_metadata

    normalized = normalize_budget(budget)
    create_workspace_folder(workspace)
    update_workspace_metadata(workspace, {"budget": normalized})
    return normalized


def workspace_period_spend(workspace: str, period: str) -> float:
    """Estimated USD spend for a workspace over the given period. Fails open to
    ``0.0`` on any error."""
    try:
        from managers import run_manager
        from common.pricing import load_price_map, run_cost_usd

        since = period_start(period)
        prices = load_price_map()
        total = 0.0
        for r in run_manager.load_runs():
            if (r.get("workspace") or "") != workspace:
                continue
            # Replay and eval runs are evaluation spend, not the workspace's
            # production spend — exclude them so evaluating an agent can't trip
            # the budget cap. (The eval runner still calls check_budget per cell,
            # so a sweep is stopped by *production* spend, not by its own.)
            if (r.get("channel") or "") in EVALUATION_CHANNELS:
                continue
            if since:
                ts = r.get("started_at") or r.get("created_at") or ""
                if ts and ts < since:
                    continue
            total += run_cost_usd(r, prices)
        return round(total, 6)
    except Exception:
        return 0.0


def budget_status(workspace: str) -> Dict[str, Any]:
    """Budget config + current period spend + soft/hard breach flags."""
    cfg = get_budget(workspace)
    spend = workspace_period_spend(workspace, cfg["period"])
    hard = cfg["hard_limit_usd"]
    soft = cfg["soft_limit_usd"]
    return {
        "workspace": workspace,
        **cfg,
        "spend": spend,
        "hard_exceeded": bool(hard) and spend >= hard,
        "soft_exceeded": bool(soft) and spend >= soft,
        "enforced": bool(hard),
    }


def check_budget(workspace: Optional[str]) -> None:
    """Raise :class:`BudgetExceededError` when a hard cap is set and already met.

    Fails open: no workspace, no hard cap, or any internal error → returns
    silently and the run proceeds. Only a clean, confident over-cap reading
    blocks the run.
    """
    if not workspace:
        return
    try:
        cfg = get_budget(workspace)
        hard = cfg["hard_limit_usd"]
        if not hard:
            return
        spend = workspace_period_spend(workspace, cfg["period"])
    except Exception:
        return
    if spend >= hard:
        raise BudgetExceededError(workspace, spend, hard, cfg["period"])
