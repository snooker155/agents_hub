"""
Cost & budget API.

Aggregates recorded agent runs into token + estimated-USD spend, grouped by
workspace / agent / model / project, and exposes per-workspace budget caps.

Spend is derived entirely from the ``runs`` table (the single source of truth
for usage) joined against catalog pricing in ``.agents_hub/models.json`` via
``common.pricing`` — the same numbers the Models usage tab shows. Budget config
lives in workspace metadata and is enforced at run launch by ``common.budget``
(see ``agents/agent_launcher.start_run``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common.costs_report import _run_cost, costs_breakdown  # noqa: F401 - _run_cost re-exported for tests
from common import budget as budget_mod
from common import audit, identity
from common.workspace_context import normalize_workspace_name

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("")
async def get_costs(
    workspace: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """Token + estimated-cost breakdowns grouped by workspace, agent, model and
    project, plus overall totals (common/costs_report.py).

    ``since`` / ``until`` are ISO date/datetime strings compared against each
    run's ``started_at``. ``workspace`` restricts to a single workspace.
    """
    return costs_breakdown(workspace, since, until)


class BudgetSettings(BaseModel):
    hard_limit_usd: float = 0.0
    soft_limit_usd: float = 0.0
    period: str = "monthly"  # total | daily | monthly
    # Default money cap of a single task's runs (0 = off). A task's own
    # budget_usd overrides it; enforced in the child by RunBudgetGuard.
    run_limit_usd: float = 0.0
    # Off by default (fails open, see common.budget). When set, a lookup or
    # pricing failure while checking the budget refuses the run instead of
    # letting it start unchecked.
    fail_closed: bool = False


@router.get("/budget")
async def get_budget(workspace: Optional[str] = None):
    """Budget config + current period spend + breach flags for a workspace."""
    ws = normalize_workspace_name(workspace) or "default"
    return budget_mod.budget_status(ws)


@router.post("/budget")
async def set_budget(request: Request, settings: BudgetSettings, workspace: Optional[str] = None):
    """Persist a workspace's budget caps and return the fresh status.

    In ``multi`` mode only an administrator sets them: a cap is how the
    service limits spend, so a workspace owner (a personal workspace's
    person included) does not get to lift their own."""
    identity.require_role(identity.request_principal(request), admin=True)
    ws = normalize_workspace_name(workspace) or "default"
    try:
        budget_mod.set_budget(ws, settings.model_dump())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    audit.record("workspace.budget", principal=identity.request_principal(request),
                 object_type="workspace", object_id=ws, workspace=ws,
                 ip=identity.client_ip(request), details=settings.model_dump())
    return budget_mod.budget_status(ws)
