"""
Per-workspace cost budgets.

A workspace can carry a ``budget`` block in its metadata::

    {"hard_limit_usd": 0.0, "soft_limit_usd": 0.0, "period": "monthly",
     "run_limit_usd": 0.0, "fail_closed": False}

``period`` is one of ``total`` / ``daily`` / ``monthly`` (UTC). A limit of ``0``
means *disabled* — the default, so existing workspaces keep their current
behaviour (this feature is opt-in). ``run_limit_usd`` is different in kind: it
is not a period cap but the default money cap of a single task's runs, applied
inside the running agent by ``common.run_budget`` and
``agents.callbacks.guards.RunBudgetGuard`` (a task's own ``budget_usd`` wins
over it). ``hard_limit_usd`` is enforced at run launch:
when the period's estimated spend already meets or exceeds it, ``check_budget``
raises :class:`BudgetExceededError` and the run is not started.

By default everything here fails open — any lookup/pricing error results in
*no* enforcement rather than a wedged workspace. ``fail_closed`` flips that for
a workspace that would rather refuse a run than risk an uncapped one: when it
is set, ``check_budget`` raises :class:`BudgetUnavailableError` (a sibling of
``BudgetExceededError``, so a caller that only distinguishes "budget breach"
from everything else keeps working) instead of returning silently on a lookup
or pricing failure, or when the price catalog is too thin to trust the
estimate (empty). Reading the budget block itself (``get_budget``) still fails
open to the disabled default on a metadata error: at that point whether the
workspace wants ``fail_closed`` is itself unknown, so there is nothing to fail
closed *to*.

Spend is estimated from recorded runs (``run_manager.load_runs``) joined against
catalog pricing (``common.pricing``); it is the same number the Costs page shows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from common.pricing import EVALUATION_CHANNELS

log = logging.getLogger(__name__)

VALID_PERIODS = ("total", "daily", "monthly")

_DEFAULT_BUDGET: Dict[str, Any] = {
    "hard_limit_usd": 0.0,
    "soft_limit_usd": 0.0,
    "period": "monthly",
    "run_limit_usd": 0.0,
    "fail_closed": False,
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


class BudgetUnavailableError(BudgetExceededError):
    """Raised instead of :class:`BudgetExceededError` when a workspace has
    ``fail_closed`` set and its spend could not be evaluated with confidence:
    a lookup or pricing failure, or a price catalog too thin to trust (empty).

    Where ``BudgetExceededError`` means "the cap was confidently read as met",
    this means "the cap could not be confidently read at all" — a workspace
    that opted into failing closed treats not knowing the same as knowing it
    is over, and refuses the run rather than guessing. ``spend`` and ``limit``
    are left ``None`` (unlike the parent class) because neither is known.
    """

    def __init__(self, workspace: str, period: str, reason: str = ""):
        self.workspace = workspace
        self.spend = None
        self.limit = None
        self.period = period
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        Exception.__init__(
            self,
            f"Workspace '{workspace}' requires fail-closed budget enforcement, "
            f"but its {period} spend could not be evaluated{detail}, and the "
            f"workspace requires it. Run not started.",
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
        "run_limit_usd": _num("run_limit_usd"),
        # Any truthy value opts in (a stored "true"/"1" survives a JSON round
        # trip as a string just as easily as a bool), so coerce rather than
        # require an exact ``True``.
        "fail_closed": bool(src.get("fail_closed")),
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
    except Exception:  # noqa: BLE001 - fails open to the disabled default (see docstring)
        log.debug("get_budget failed for %s", workspace, exc_info=True)
        return dict(_DEFAULT_BUDGET)


def set_budget(workspace: str, budget: Any) -> Dict[str, Any]:
    """Persist a workspace's budget config; returns the normalized value."""
    from workspace import create_workspace_folder, update_workspace_metadata

    normalized = normalize_budget(budget)
    create_workspace_folder(workspace)
    update_workspace_metadata(workspace, {"budget": normalized})
    return normalized


def _workspace_period_spend_strict(workspace: str, period: str) -> tuple[float, int]:
    """Raw computation behind :func:`workspace_period_spend`, exceptions and
    all: a lookup or pricing failure propagates instead of reading as ``$0``.

    Used by :func:`check_budget` under ``fail_closed``, where that silent
    ``$0`` would be exactly wrong — it would read as "under the cap" when the
    truth is "unknown". Returns ``(spend, priced_models)`` so the caller can
    also tell an empty price catalog (nothing was ever priced, so *any*
    estimate is unreliable) from a confident zero.
    """
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
    return round(total, 6), len(prices)


def workspace_period_spend(workspace: str, period: str) -> float:
    """Estimated USD spend for a workspace over the given period. Fails open to
    ``0.0`` on any error."""
    try:
        total, _priced = _workspace_period_spend_strict(workspace, period)
        return total
    except Exception:  # noqa: BLE001 - fails open to 0.0 (see docstring)
        log.debug("workspace_period_spend failed for %s", workspace, exc_info=True)
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

    Fails open by default: no workspace, no hard cap, or any internal error
    (while reading the config) returns silently and the run proceeds. Once a
    hard cap is configured, a workspace with ``fail_closed`` set changes that:
    a lookup or pricing failure while estimating spend, or a price catalog too
    thin to trust (empty), raises :class:`BudgetUnavailableError` instead of
    reading as "under the cap". ``get_budget`` itself still fails open (see its
    docstring): if the config cannot even be read, ``fail_closed`` is unknown,
    so there is nothing to fail closed to.
    """
    if not workspace:
        return
    try:
        cfg = get_budget(workspace)
    except Exception:  # noqa: BLE001 - fails open (see docstring): fail_closed itself is unknown here
        log.debug("check_budget could not read the budget config for %s", workspace, exc_info=True)
        return
    hard = cfg["hard_limit_usd"]
    if not hard:
        return
    fail_closed = bool(cfg.get("fail_closed"))
    try:
        spend, priced_models = _workspace_period_spend_strict(workspace, cfg["period"])
    except Exception as exc:  # noqa: BLE001 - fail_closed decides whether this blocks the run
        log.debug("check_budget failed for %s", workspace, exc_info=True)
        if fail_closed:
            raise BudgetUnavailableError(
                workspace, cfg["period"],
                reason="the spend estimate could not be computed",
            ) from exc
        return
    if fail_closed and not priced_models:
        raise BudgetUnavailableError(
            workspace, cfg["period"],
            reason="the price catalog is empty",
        )
    if spend >= hard:
        raise BudgetExceededError(workspace, spend, hard, cfg["period"])
