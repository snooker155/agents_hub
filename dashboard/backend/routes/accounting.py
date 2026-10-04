"""
Spend report by API key, user, project, workspace, agent or model
(docs/costs.md "Report").

Two sources, one row shape. A *run* (an agent/task run, and the top-level run
of a flow, loop, team or scenario — the same rows ``routes/costs.py`` prices)
carries who launched it (``launched_by``), the personal key it was launched
with if any (``key_id``) and the project of its task if any (``project_id``);
a served ``/v1`` completion (``common/serving.py``) carries only who called it
and which key, since the endpoint answers with a model, not a workspace or a
project. Grouping by workspace, project or agent therefore counts runs only —
a served call has none of those dimensions to bucket it by, so it is left out
of those groupings rather than misfiled under "(none)".

Evaluation channels (replay, eval) are excluded, the same rule
``routes/costs.py`` and ``common/budget.py`` follow: measuring an agent is not
production spend. A non-administrator in ``multi`` mode sees only their own
rows (by ``launched_by`` / the served call's user id) regardless of the
grouping asked for — who spent what is not every member's business, the same
rule ``GET /api/models/serving/usage`` already applies.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agents import registry
from common import api_keys, identity
from common.pricing import (EVALUATION_CHANNELS, load_price_map, run_cached_tokens,
                            run_cost_usd, run_tokens, container_cost_usd)
from managers import run_manager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounting", tags=["accounting"])

GROUP_BY_CHOICES = ("key", "user", "project", "workspace", "agent", "model")

_CSV_COLUMNS = ("key", "label", "runs", "calls", "inbound_tokens", "cached_tokens",
                "outbound_tokens", "total_tokens", "cost")


# ── helpers shared with routes/costs.py's own report ─────────────────────────

def _in_range(ts: str, since: Optional[str], until: Optional[str]) -> bool:
    if since and ts and ts < since:
        return False
    if until and ts and ts > until:
        return False
    return True


def _run_cost(run: Dict[str, Any], prices) -> float:
    """A run's spend: its own reported cost when it has one (a wrapped CLI
    that prices its own call), catalog pricing otherwise. Mirrors
    ``routes/costs.py._run_cost``."""
    reported = run.get("reported_cost_usd")
    if isinstance(reported, (int, float)) and not isinstance(reported, bool):
        # The wrapped service prices its own model calls, not the container
        # the hub ran it in, so the container hours are still added.
        return float(reported) + container_cost_usd(run)
    return run_cost_usd(run, prices)


def _user_label(user_id: Optional[str]) -> str:
    """A run without a stamped ``launched_by`` is one recorded before this
    feature shipped (docs/costs.md "Attribution"): unattributable, not "no
    one" — the label says so rather than showing a blank row."""
    if not user_id:
        return "(unknown)"
    user = identity.get_user(user_id)
    if user is None:
        return str(user_id)
    return user.get("display_name") or user.get("username") or str(user_id)


def _key_label(key_id: Optional[str]) -> str:
    if not key_id:
        return "(none)"
    key = api_keys.get_key(key_id)
    if key is None:
        return "(revoked key)"
    return key.get("name") or (f"…{key['hint']}" if key.get("hint") else key_id)


def _project_label(project_id: Optional[str], projects: Dict[str, Any]) -> str:
    if not project_id:
        return "(none)"
    project = projects.get(project_id)
    return getattr(project, "name", None) or project_id


def _load_projects() -> Dict[str, Any]:
    try:
        from projects.storage import ProjectStore
        return {p.id: p for p in ProjectStore().list()}
    except Exception:  # noqa: BLE001 - a project name is a label, never required to build the report
        log.debug("accounting: could not load project names", exc_info=True)
        return {}


def _load_agent_names() -> Dict[str, str]:
    try:
        return {a.id: getattr(a, "name", a.id) for a in registry.list_agents()}
    except Exception:  # noqa: BLE001 - see _load_projects
        return {}


def _run_bucket(group_by: str, run: Dict[str, Any], *, projects: Dict[str, Any],
                agent_names: Dict[str, str]) -> Tuple[str, str]:
    if group_by == "key":
        key_id = str(run.get("key_id") or "")
        return key_id or "(none)", _key_label(key_id)
    if group_by == "user":
        user_id = str(run.get("launched_by") or "")
        return user_id or "(unknown)", _user_label(user_id)
    if group_by == "project":
        project_id = str(run.get("project_id") or "")
        return project_id or "(none)", _project_label(project_id, projects)
    if group_by == "workspace":
        ws = (run.get("workspace") or "").strip() or "(none)"
        return ws, ws
    if group_by == "agent":
        agent_id = (run.get("agent_id") or "").strip() or "(unknown)"
        return agent_id, agent_names.get(agent_id, agent_id)
    # model
    provider = (run.get("provider") or "").strip() or "unknown"
    model = (run.get("model") or "").strip() or "(untracked)"
    label = f"{provider}/{model}"
    return label, label


def _serving_bucket(group_by: str, row: Any) -> Optional[Tuple[str, str]]:
    """None for a grouping ``serving_usage`` has no column for (project,
    workspace, agent): a served completion answers with a model, not those,
    so it is left out of those groupings rather than filed under "(none)"."""
    if group_by == "key":
        key_id = str(row["key_id"] or "")
        return key_id or "(none)", _key_label(key_id)
    if group_by == "user":
        user_id = str(row["user_id"] or "")
        return user_id or "(unknown)", _user_label(user_id)
    if group_by == "model":
        provider = row["provider"] or "unknown"
        model = row["model"] or "(untracked)"
        label = f"{provider}/{model}"
        return label, label
    return None


def _serving_rows(since: Optional[str], until: Optional[str], *, user_id: Optional[str] = None):
    from common import db
    clauses: List[str] = []
    args: List[Any] = []
    if since:
        clauses.append("at >= ?")
        args.append(since)
    if until:
        clauses.append("at <= ?")
        args.append(until)
    if user_id:
        clauses.append("user_id = ?")
        args.append(str(user_id))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return db.get_conn().execute(
        f"SELECT user_id, key_id, provider, model, prompt_tokens, completion_tokens, "
        f"cost_usd FROM serving_usage{where}", tuple(args)).fetchall()


def _bump(buckets: Dict[str, Dict[str, Any]], key: str, label: str, *,
         runs: int = 0, calls: int = 0, inbound: int = 0, outbound: int = 0,
         cached: int = 0, cost: float = 0.0) -> None:
    bucket = buckets.get(key)
    if bucket is None:
        bucket = buckets[key] = {
            "key": key, "label": label, "runs": 0, "calls": 0,
            "inbound_tokens": 0, "cached_tokens": 0, "outbound_tokens": 0,
            "total_tokens": 0, "cost": 0.0,
        }
    bucket["runs"] += runs
    bucket["calls"] += calls
    bucket["inbound_tokens"] += inbound
    bucket["cached_tokens"] += cached
    bucket["outbound_tokens"] += outbound
    bucket["total_tokens"] += inbound + outbound
    bucket["cost"] += cost


def _build_report(group_by: str, *, since: Optional[str], until: Optional[str],
                  workspace: Optional[str], principal: Any) -> List[Dict[str, Any]]:
    if group_by not in GROUP_BY_CHOICES:
        raise HTTPException(status_code=400,
                            detail=f"group_by must be one of: {', '.join(GROUP_BY_CHOICES)}")

    # A non-administrator in multi mode only ever sees their own spend, the
    # same rule GET /api/models/serving/usage already applies: who spent what
    # is not every member's business.
    own_user_id: Optional[str] = None
    if identity.current_mode() == "multi" and not getattr(principal, "is_admin", False):
        own_user_id = getattr(principal, "id", None) or "-"

    prices = load_price_map()
    projects = _load_projects()
    agent_names = _load_agent_names()
    buckets: Dict[str, Dict[str, Any]] = {}

    for run in run_manager.load_runs():
        if (run.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        ws = (run.get("workspace") or "").strip()
        if workspace and ws != workspace:
            continue
        if own_user_id and str(run.get("launched_by") or "") != str(own_user_id):
            continue
        ts = run.get("started_at") or run.get("created_at") or ""
        if not _in_range(ts, since, until):
            continue
        inbound, outbound = run_tokens(run)
        cached = max(0, min(run_cached_tokens(run), inbound))
        key, label = _run_bucket(group_by, run, projects=projects, agent_names=agent_names)
        _bump(buckets, key, label, runs=1, inbound=inbound, outbound=outbound,
              cached=cached, cost=_run_cost(run, prices))

    if not workspace:
        # A served completion carries no workspace: once the caller scoped
        # the report to one, none of these calls can be honestly attributed
        # to it, so they are left out entirely rather than counted anyway.
        for row in _serving_rows(since, until, user_id=own_user_id):
            bucket = _serving_bucket(group_by, row)
            if bucket is None:
                continue
            key, label = bucket
            _bump(buckets, key, label, calls=1,
                  inbound=int(row["prompt_tokens"] or 0),
                  outbound=int(row["completion_tokens"] or 0),
                  cost=float(row["cost_usd"] or 0.0))

    rows = list(buckets.values())
    for row in rows:
        row["cost"] = round(row["cost"], 4)
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows


def _totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "runs": sum(r["runs"] for r in rows),
        "calls": sum(r["calls"] for r in rows),
        "inbound_tokens": sum(r["inbound_tokens"] for r in rows),
        "cached_tokens": sum(r["cached_tokens"] for r in rows),
        "outbound_tokens": sum(r["outbound_tokens"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 4),
    }


def _csv_response(rows: List[Dict[str, Any]]) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for row in rows:
        writer.writerow([row[c] for c in _CSV_COLUMNS])
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="accounting-report-{stamp}.csv"'})


@router.get("/report")
async def get_report(request: Request, group_by: str = "user", since: Optional[str] = None,
                     until: Optional[str] = None, workspace: Optional[str] = None,
                     format: Optional[str] = None):
    """Spend grouped by key, user, project, workspace, agent or model,
    combining runs and served ``/v1`` calls. ``format=csv`` downloads the same
    rows as a file instead of returning JSON."""
    principal = identity.request_principal(request)
    rows = _build_report(group_by, since=since, until=until, workspace=workspace,
                         principal=principal)
    if (format or "").lower() == "csv":
        return _csv_response(rows)
    return {"group_by": group_by, "rows": rows, "totals": _totals(rows)}
