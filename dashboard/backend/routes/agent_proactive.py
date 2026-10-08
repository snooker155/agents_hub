"""
Routes: the proactive profile and the Pulse tab (proactive/, docs/proactive.md).

``GET/PUT /api/agents/{agent_id}/proactive`` read and write the agent's
profile the way ``routes/agent_loop_settings.py`` handles the loop settings:
a partial PUT changes only the keys it carries, validation lives in
``proactive.profile.validate_profile``, the save goes through
``proactive.service.save_profile`` so the heartbeat job in plans/ is created,
updated or cancelled in the same call. Both answer with everything the Pulse
tab shows: the profile, the job, today's usage and the newest ticks.

``POST .../proactive/pause``, ``.../resume`` and ``.../wake`` act on the job
the profile owns, so the tab never has to know the job id. ``wake`` fires a
tick now (``trigger="manual"``), through the quiet hours but not past the
budget, the tick limit or a tick still running.

Roles (multi mode): reading a pulse needs viewer on the workspace it runs in,
changing, pausing, resuming or waking it needs editor there, and a PUT that
moves the pulse to another workspace needs editor on that one too. The
Dashboard summary shows only the pulses of workspaces the caller can see.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from common import access, audit, identity
from common.auth import WS_EDITOR, WS_VIEWER
from proactive import service as proactive

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent_proactive"])


def _status_or_404(agent_id: str, limit: int = 50) -> Dict[str, Any]:
    body = proactive.status(agent_id, limit=limit)
    if body is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return body


def _require(request: Optional[Request], agent_id: str, role: str) -> None:
    """404 for an unknown agent, else the caller's role on the pulse's workspace."""
    workspace = proactive.workspace_of(agent_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    identity.require_role(identity.request_principal(request), workspace=workspace, role=role)


def _visible_summary(body: Dict[str, Any], request: Optional[Request]) -> Dict[str, Any]:
    """The summary cut down to the pulses this caller can see, totals redone."""
    principal = identity.request_principal(request)
    if access.visible_workspaces(principal) is None:
        return body
    rows = access.filter_by_workspace(principal, body.get("agents") or [])
    totals = {key: 0 for key in body.get("totals") or {}}
    for row in rows:
        totals["agents"] = totals.get("agents", 0) + 1
        totals["paused"] = totals.get("paused", 0) + (1 if row.get("status") == "paused" else 0)
        for key in totals:
            if key not in ("agents", "paused"):
                totals[key] += int(row.get(key) or 0)
    return {**body, "agents": rows, "totals": totals}


def _audit(action: str, request: Request, agent_id: str, details: Dict[str, Any]) -> None:
    try:
        profile = proactive.get_profile(agent_id) or {}
        audit.record(action, principal=identity.request_principal(request),
                     object_type="agent", object_id=agent_id,
                     workspace=profile.get("workspace"),
                     ip=identity.client_ip(request), details=details)
    except Exception:  # noqa: BLE001 - an audit failure must not undo the change
        log.warning("could not record the %s audit entry for %s", action, agent_id, exc_info=True)


@router.get("/api/proactive/summary")
async def proactive_summary(workspace: Optional[str] = None, hours: int = 24, request: Request = None):
    """The Dashboard widget: every pulse that is on, and what its ticks of
    the last ``hours`` came to (acted, quiet, blocked, error, skipped)."""
    if workspace:
        identity.require_role(identity.request_principal(request), workspace=workspace, role=WS_VIEWER)
    body = proactive.summary(workspace or None, hours=max(1, min(int(hours), 24 * 30)))
    return _visible_summary(body, request)


@router.get("/api/agents/{agent_id}/proactive")
async def get_proactive(agent_id: str, limit: int = 50, request: Request = None):
    _require(request, agent_id, WS_VIEWER)
    return _status_or_404(agent_id, limit=limit)


@router.put("/api/agents/{agent_id}/proactive")
async def update_proactive(agent_id: str, patch: Dict[str, Any], request: Request):
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="the profile must be a JSON object")
    _require(request, agent_id, WS_EDITOR)
    before = proactive.get_profile(agent_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    moved_to = patch.get("workspace")
    if moved_to and moved_to != before.get("workspace"):
        identity.require_role(identity.request_principal(request), workspace=str(moved_to), role=WS_EDITOR)
    try:
        principal = identity.request_principal(request)
        after = proactive.save_profile(
            agent_id, patch, actor=str(getattr(principal, "username", None) or "") or None,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    changed = {k: after.get(k) for k in patch if before.get(k) != after.get(k)}
    if before.get("enabled") != after.get("enabled"):
        action = "agent.proactive.enabled" if after.get("enabled") else "agent.proactive.disabled"
    else:
        action = "agent.proactive.changed"
    _audit(action, request, agent_id, {"changed": changed, "job_id": after.get("job_id")})
    return _status_or_404(agent_id)


@router.post("/api/agents/{agent_id}/proactive/pause")
async def pause_proactive(agent_id: str, request: Request):
    _require(request, agent_id, WS_EDITOR)
    try:
        proactive.pause(agent_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _audit("agent.proactive.paused", request, agent_id, {})
    return _status_or_404(agent_id)


@router.post("/api/agents/{agent_id}/proactive/resume")
async def resume_proactive(agent_id: str, request: Request):
    _require(request, agent_id, WS_EDITOR)
    try:
        proactive.resume(agent_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _audit("agent.proactive.resumed", request, agent_id, {})
    return _status_or_404(agent_id)


@router.post("/api/agents/{agent_id}/proactive/wake")
async def wake_proactive(agent_id: str, request: Request):
    _require(request, agent_id, WS_EDITOR)
    try:
        result = proactive.wake(agent_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _audit("agent.proactive.woken", request, agent_id, {"result": result})
    body = _status_or_404(agent_id)
    body["result"] = result
    return body
