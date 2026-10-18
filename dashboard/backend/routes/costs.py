"""
Cost API.

Aggregates recorded agent runs into token + estimated-USD spend, grouped by
workspace / agent / model / project.

Spend is derived entirely from the ``runs`` table (the single source of truth
for usage) joined against catalog pricing in ``.agents_hub/models.json`` via
``common.pricing``, the same numbers the Models usage tab shows.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from common.costs_report import RUN_SORTS, _run_cost, costs_breakdown, costs_runs  # noqa: F401 - _run_cost re-exported for tests
from common import access
from common import identity

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


@router.get("/runs")
async def get_cost_runs(
    request: Request,
    workspace: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = None,
    order: Optional[str] = None,
    limit: int = Query(20, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """The runs the breakdowns above add up, each with its tokens and
    estimated cost, under the same filters. ``sort`` is ``started``
    (default), ``cost``, ``tokens``, ``agent`` or ``model``; ``order`` is
    ``asc`` or ``desc``. ``agent_id`` and ``status`` narrow the list. Rows are limited to the workspaces the caller may
    see (common/access.py), since a run's title and agent are its own record."""
    if sort and sort not in RUN_SORTS:
        raise HTTPException(status_code=400, detail=f"Unknown sort '{sort}'")
    return costs_runs(
        workspace, since, until,
        workspaces=access.visible_workspaces(identity.request_principal(request)),
        agent_id=agent_id or None, status=status or None,
        sort=sort or "started", order="asc" if order == "asc" else "desc",
        limit=limit, offset=offset,
    )
