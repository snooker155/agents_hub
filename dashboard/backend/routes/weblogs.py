"""
Web access log API — review what ``web_search`` and ``fetch_url`` actually read.

- ``GET    /api/web-logs``            newest-first, filterable page of calls
- ``GET    /api/web-logs/stats``      volume, failures, flagged calls, top hosts
- ``GET    /api/web-logs/{entry_id}`` one call including the stored response text
- ``DELETE /api/web-logs``            clear the log

The list rows omit response bodies — a page of fetches would otherwise be
megabytes. Fetch a single entry to read what the agent was handed, the text the
page hid from human readers, and the security flags raised against both.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from tools import web_log

router = APIRouter(prefix="/api/web-logs", tags=["web-logs"])


@router.get("")
async def list_web_logs(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    kind: Optional[str] = Query(None, description="search | fetch"),
    status: Optional[str] = Query(None, description="ok | refused | error | unsupported | not_configured"),
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    min_severity: Optional[str] = Query(None, description="low | medium | high"),
    search: Optional[str] = Query(None, description="Substring of the query, URL, agent or error"),
):
    if kind and kind not in ("search", "fetch"):
        raise HTTPException(status_code=400, detail="kind must be 'search' or 'fetch'")
    if min_severity and min_severity not in web_log.SEVERITY_ORDER:
        raise HTTPException(status_code=400, detail="min_severity must be low, medium or high")
    return web_log.query(
        limit=limit,
        offset=offset,
        kind=kind,
        status=status,
        workspace=workspace,
        agent_id=agent_id,
        min_severity=min_severity,
        search=search,
    )


@router.get("/stats")
async def web_log_stats():
    return web_log.stats()


@router.get("/{entry_id}")
async def get_web_log(entry_id: str):
    entry = web_log.get(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return entry


@router.delete("")
async def clear_web_logs():
    return {"ok": True, "deleted": web_log.clear()}
