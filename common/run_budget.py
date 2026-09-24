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
summed over the task's recorded runs, evaluation channels excluded. Everything
here fails open: a pricing, lookup or catalog error produces no cap rather than
a task that cannot start.
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

# Enough to cover every run a task can plausibly accumulate (resumes, retries,
# reviews) without reading the whole runs table.
_MAX_TASK_RUNS = 5000


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


def task_spend_usd(task_id: Any) -> float:
    """Estimated USD spent by every recorded run of a task.

    The same pricing the Costs page and the workspace budget use, with replay
    and eval runs left out (they measure the agent, they are not the task's
    work). Fails open to ``0.0``.
    """
    if not task_id:
        return 0.0
    try:
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


def launch_env(task: Any, ws_name: str | None) -> Dict[str, str]:
    """Environment variables that carry the task's money cap into its run.

    ``{}`` when the task has no cap. The launch is never refused here, even
    when the task has already spent its cap: the guard in the child parks the
    run on its first LLM call instead, which leaves one place that decides and
    one shape (a parked task) for the operator to answer. Any error returns
    ``{}`` so a broken catalog never blocks a launch.
    """
    try:
        cap = effective_cap(task, ws_name)
        if cap <= 0:
            return {}
        spent = task_spend_usd(getattr(task, "id", None))
        return {
            ENV_LIMIT: repr(float(cap)),
            ENV_SPENT: repr(float(spent)),
            ENV_PRICES: json.dumps(price_rows(), separators=(",", ":")),
        }
    except Exception:  # noqa: BLE001 - fails open (see docstring)
        log.debug("run budget launch_env failed for task %s", getattr(task, "id", None), exc_info=True)
        return {}
