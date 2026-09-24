"""
Per-task money cap: how much one task's runs may spend before the run pauses.

The workspace budget (``common.budget``) is a period cap checked once, at
launch, against the whole workspace's spend. That protects the month, not the
task: a single runaway run can burn the entire monthly allowance in one sitting
before the next launch is ever checked. This module is the other half, a cap on
one task, enforced *during* the run.

Where the cap comes from:

- ``task.budget_usd`` when it is set: ``0`` means the task is deliberately
  uncapped, any positive number is its cap.
- otherwise the workspace's ``run_limit_usd`` from its budget block (``0`` means
  off, the default).

How it is enforced: the launcher (``agents.agent_launcher._launch_extras``)
calls :func:`launch_env`, which turns the cap, the task's spend so far and the
catalog prices into three environment variables for the child process. The
child builds ``agents.callbacks.guards.RunBudgetGuard`` from them and prices
every LLM call itself as it finishes, so a run in a container with no database
access can still stop itself. When the cap is reached the run parks as
``awaiting_approval`` with a ``kind: "budget"`` pending record, and the operator
either raises the cap (the task resumes) or stops it (the approve route).

Spend is the same number the Costs page shows: ``common.pricing.run_cost_usd``
summed over the task's recorded runs, evaluation channels excluded. By default
everything here fails open: a pricing, lookup or catalog error produces no cap
rather than a task that cannot start.

A workspace with ``fail_closed`` set (``common.budget``) changes that: when the
cap or the task's spend so far cannot be computed, :func:`launch_env` raises
:class:`RunBudgetUnavailableError` instead of returning ``{}`` (which a caller
that does not inspect the exception cannot tell apart from "no cap"), so the
launch is refused rather than started with an unknown, unenforced cap. The
flag also rides along to the child as ``AGENTS_HUB_RUN_BUDGET_FAIL_CLOSED``:
``RunBudgetGuard`` uses it to treat a call priced through an unmatched model as
blocking rather than free, since counting it as free would let the cap it is
supposed to enforce silently never trip.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from common.pricing import EVALUATION_CHANNELS

log = logging.getLogger(__name__)

#: The cap in USD, read by ``RunBudgetGuard.from_env``.
ENV_LIMIT = "AGENTS_HUB_RUN_BUDGET_USD"
#: What the task's earlier runs already spent, so a resumed run starts counting
#: from there instead of from zero (otherwise raising the cap by a cent would buy
#: a whole new cap's worth of calls).
ENV_SPENT = "AGENTS_HUB_RUN_BUDGET_SPENT_USD"
#: JSON list of ``[provider, model, input, output, cached_input]`` (USD per 1M
#: tokens), the catalog prices the child uses to price its own calls.
ENV_PRICES = "AGENTS_HUB_RUN_PRICES"
#: Set (to "1") when the workspace has ``fail_closed`` on, so the child's
#: ``RunBudgetGuard`` treats an unpriced call as blocking rather than free.
ENV_FAIL_CLOSED = "AGENTS_HUB_RUN_BUDGET_FAIL_CLOSED"

# Enough to cover every run a task can plausibly accumulate (resumes, retries,
# reviews) without reading the whole runs table.
_MAX_TASK_RUNS = 5000


class RunBudgetUnavailableError(RuntimeError):
    """Raised by :func:`launch_env` when the workspace has ``fail_closed`` set
    and the task's money cap or spend so far could not be computed.

    Mirrors ``common.budget.BudgetUnavailableError``: an unreadable cap is
    treated the same as an unenforceable one, refusing the launch instead of
    starting a run nothing will stop.
    """

    def __init__(self, task_id: Any, reason: str = ""):
        self.task_id = task_id
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(
            f"The run budget for task {task_id} could not be evaluated{detail}, "
            f"and its workspace requires fail-closed enforcement. Run not started."
        )


def _positive(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def effective_cap(task: Any, ws_name: str | None) -> float:
    """The money cap for this task's runs in USD, ``0.0`` meaning none.

    ``task.budget_usd`` wins when it is not None, including an explicit ``0``
    (a task someone marked as uncapped stays uncapped even in a workspace with
    a default). Otherwise the workspace's ``run_limit_usd`` applies. Fails open
    to ``0.0``.
    """
    try:
        own = getattr(task, "budget_usd", None)
        if own is not None:
            return _positive(own)
        if not ws_name:
            return 0.0
        from common.budget import get_budget

        return _positive(get_budget(ws_name).get("run_limit_usd"))
    except Exception:  # noqa: BLE001 - fails open to no cap (see module docstring)
        log.debug("effective_cap failed for task %s", getattr(task, "id", None), exc_info=True)
        return 0.0


def _task_spend_usd_strict(task_id: Any) -> float:
    """Raw computation behind :func:`task_spend_usd`, exceptions and all.

    Used by :func:`launch_env`, which decides itself (via ``fail_closed`)
    whether a failure here should refuse the launch or, as ``task_spend_usd``
    does for every other caller, read as ``$0``.
    """
    if not task_id:
        return 0.0
    from common.pricing import load_price_map, run_cost_usd
    from managers import run_manager

    page = run_manager.query_runs(task_id=str(task_id), limit=_MAX_TASK_RUNS)
    prices = load_price_map()
    total = 0.0
    for run in page.get("items") or []:
        if (run.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        total += run_cost_usd(run, prices)
    return round(total, 6)


def task_spend_usd(task_id: Any) -> float:
    """Estimated USD spent by every recorded run of a task.

    The same pricing the Costs page and the workspace budget use, with replay
    and eval runs left out (they measure the agent, they are not the task's
    work). Fails open to ``0.0``.
    """
    try:
        return _task_spend_usd_strict(task_id)
    except Exception:  # noqa: BLE001 - fails open to 0.0 (see module docstring)
        log.debug("task_spend_usd failed for %s", task_id, exc_info=True)
        return 0.0


def price_rows() -> List[List[Any]]:
    """The catalog prices as JSON-friendly rows for :data:`ENV_PRICES`."""
    from common.pricing import load_price_map

    return [
        [provider, model, float(i), float(o), float(c)]
        for (provider, model), (i, o, c) in sorted(load_price_map().items())
    ]


def _workspace_fail_closed(ws_name: str | None) -> bool:
    """Whether ``ws_name``'s budget block has ``fail_closed`` set.

    Fails open to ``False``: if the workspace config cannot even be read,
    there is no ``fail_closed`` preference to honour (same reasoning as
    ``common.budget.check_budget`` for its own ``get_budget`` call).
    """
    if not ws_name:
        return False
    try:
        from common.budget import get_budget

        return bool(get_budget(ws_name).get("fail_closed"))
    except Exception:  # noqa: BLE001 - fails open to False (see docstring)
        return False


def launch_env(task: Any, ws_name: str | None) -> Dict[str, str]:
    """Environment variables that carry the task's money cap into its run.

    ``{}`` when the task has no cap. The launch is never refused here for a
    task that has already spent its cap, even under ``fail_closed``: the
    guard in the child parks the run on its first LLM call instead, which
    leaves one place that decides and one shape (a parked task) for the
    operator to answer.

    What ``fail_closed`` does change is a computation failure. By default any
    error here returns ``{}`` so a broken catalog never blocks a launch — but
    ``{}`` also means "no cap", indistinguishable from a task that was never
    capped at all. A workspace with ``fail_closed`` set would rather refuse the
    launch than let a capped task start with its cap silently gone, so this
    raises :class:`RunBudgetUnavailableError` instead in that case.
    """
    fail_closed = _workspace_fail_closed(ws_name)
    try:
        cap = effective_cap(task, ws_name)
        if cap <= 0:
            return {}
        spent = _task_spend_usd_strict(getattr(task, "id", None))
        env = {
            ENV_LIMIT: repr(float(cap)),
            ENV_SPENT: repr(float(spent)),
            ENV_PRICES: json.dumps(price_rows(), separators=(",", ":")),
        }
        if fail_closed:
            env[ENV_FAIL_CLOSED] = "1"
        return env
    except Exception as exc:  # noqa: BLE001 - fail_closed decides whether this blocks the launch
        log.debug("run budget launch_env failed for task %s", getattr(task, "id", None), exc_info=True)
        if fail_closed:
            raise RunBudgetUnavailableError(getattr(task, "id", None)) from exc
        return {}
