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

from managers import run_manager
from agents import registry
from tasks import service as tasks_service
from common.pricing import (EVALUATION_CHANNELS, load_price_map, run_cached_tokens,
                            run_cost_usd, run_tokens)
from common import budget as budget_mod
from common import audit, identity
from common.workspace_context import normalize_workspace_name

router = APIRouter(prefix="/api/costs", tags=["costs"])


def _in_range(ts: str, since: Optional[str], until: Optional[str]) -> bool:
    if since and ts and ts < since:
        return False
    if until and ts and ts > until:
        return False
    return True


@router.get("")
async def get_costs(
    workspace: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """Token + estimated-cost breakdowns grouped by workspace, agent, model and
    project, plus overall totals.

    ``since`` / ``until`` are ISO date/datetime strings compared against each
    run's ``started_at``. ``workspace`` restricts to a single workspace.
    """
    runs = run_manager.load_runs()
    prices = load_price_map()

    # task_id -> project_id, to attribute spend to projects. Best-effort: a run
    # with no task or an unmapped task lands in the "(none)" bucket.
    task_project: dict = {}
    try:
        for t in tasks_service.list_tasks():
            task_project[str(getattr(t, "id", ""))] = getattr(t, "project_id", None) or ""
    except Exception:
        pass

    agent_names = {}
    try:
        agent_names = {a.id: getattr(a, "name", a.id) for a in registry.list_agents()}
    except Exception:
        pass

    by_workspace: dict = {}
    by_agent: dict = {}
    by_model: dict = {}
    by_project: dict = {}
    totals = {"runs": 0, "inbound_tokens": 0, "cached_tokens": 0,
              "outbound_tokens": 0, "total_tokens": 0, "cost": 0.0}

    def _bump(bucket: dict, key: str, label: str, run: dict,
              inbound: int, outbound: int, cached: int, cost: float):
        b = bucket.get(key)
        if b is None:
            b = bucket[key] = {
                "key": key, "label": label,
                "runs": 0, "inbound_tokens": 0, "cached_tokens": 0,
                "outbound_tokens": 0, "total_tokens": 0, "cost": 0.0,
            }
        b["runs"] += 1
        b["inbound_tokens"] += inbound
        # Reported alongside, not on top of, inbound: these are the inbound
        # tokens the provider served from its cache at the cheaper rate.
        b["cached_tokens"] += cached
        b["outbound_tokens"] += outbound
        b["total_tokens"] += inbound + outbound
        b["cost"] += cost

    for r in runs:
        # Replay and eval runs are evaluation, not production spend — never
        # count them. Both channels are tagged at run creation for exactly this.
        if (r.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        ws = (r.get("workspace") or "").strip()
        if workspace and ws != workspace:
            continue
        ts = r.get("started_at") or r.get("created_at") or ""
        if not _in_range(ts, since, until):
            continue

        inbound, outbound = run_tokens(r)
        cached = max(0, min(run_cached_tokens(r), inbound))
        cost = run_cost_usd(r, prices)

        agent_id = (r.get("agent_id") or "").strip() or "(unknown)"
        model = (r.get("model") or "").strip() or "(untracked)"
        provider = (r.get("provider") or "").strip() or "unknown"
        project = task_project.get(str(r.get("task_id") or ""), "") or "(none)"

        _bump(by_workspace, ws or "(none)", ws or "(none)", r, inbound, outbound, cached, cost)
        _bump(by_agent, agent_id, agent_names.get(agent_id, agent_id), r, inbound, outbound, cached, cost)
        _bump(by_model, f"{provider}/{model}", f"{provider}/{model}", r, inbound, outbound, cached, cost)
        _bump(by_project, project, project, r, inbound, outbound, cached, cost)

        totals["runs"] += 1
        totals["inbound_tokens"] += inbound
        totals["cached_tokens"] += cached
        totals["outbound_tokens"] += outbound
        totals["total_tokens"] += inbound + outbound
        totals["cost"] += cost

    def _rows(bucket: dict) -> list:
        rows = list(bucket.values())
        for row in rows:
            row["cost"] = round(row["cost"], 4)
        rows.sort(key=lambda x: x["cost"], reverse=True)
        return rows

    totals["cost"] = round(totals["cost"], 4)
    return {
        "totals": totals,
        "by_workspace": _rows(by_workspace),
        "by_agent": _rows(by_agent),
        "by_model": _rows(by_model),
        "by_project": _rows(by_project),
    }


class BudgetSettings(BaseModel):
    hard_limit_usd: float = 0.0
    soft_limit_usd: float = 0.0
    period: str = "monthly"  # total | daily | monthly


@router.get("/budget")
async def get_budget(workspace: Optional[str] = None):
    """Budget config + current period spend + breach flags for a workspace."""
    ws = normalize_workspace_name(workspace) or "default"
    return budget_mod.budget_status(ws)


@router.post("/budget")
async def set_budget(request: Request, settings: BudgetSettings, workspace: Optional[str] = None):
    """Persist a workspace's budget caps and return the fresh status."""
    ws = normalize_workspace_name(workspace) or "default"
    try:
        budget_mod.set_budget(ws, settings.model_dump())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    audit.record("workspace.budget", principal=identity.request_principal(request),
                 object_type="workspace", object_id=ws, workspace=ws,
                 ip=identity.client_ip(request), details=settings.model_dump())
    return budget_mod.budget_status(ws)
