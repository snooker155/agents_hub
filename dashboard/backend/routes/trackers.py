"""
Tracker connector API: a project's Jira/Linear link, sync, and a
project/team picker for the Projects page.

Credential config (base_url/email/api_token for Jira, api_key for Linear)
and the connection test already go through the generic credential routes in
dashboard/backend/routes/connectors.py (/api/connectors/jira,
/api/connectors/linear) — this module only covers the per-project link and
the two calls that need a resolved provider client.

Endpoints:
- GET  /api/trackers/projects/{project_id}        — the project's tracker config
- PUT  /api/trackers/projects/{project_id}        — set provider/remote_id/url
- POST /api/trackers/projects/{project_id}/sync   — import/refresh tracker issues as tasks
- GET  /api/trackers/status                      — which trackers have credentials, for a picker
- GET  /api/trackers/{provider}/projects          — Jira projects / Linear teams, for a picker

A sync uses the tracker connector of the project's workspace (its own, else
the default workspace's; connectors/channels/store.py). The picker lists what
the connector in effect in ``?workspace=`` reaches (the default's when
omitted), so pass the project's workspace.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common import access, identity, record_access
from common.paths import PROJECTS_FILE
from connectors.trackers.providers import TrackerError, get_provider
from connectors.trackers.sync import sync_tracker_issues
from projects.storage import ProjectStore


router = APIRouter(prefix="/api/trackers", tags=["trackers"])

_PROVIDERS = ("none", "jira", "linear")

_store = ProjectStore(path=PROJECTS_FILE)


class TrackerConfigUpdate(BaseModel):
    provider: str
    remote_id: Optional[str] = None
    url: Optional[str] = None


def _get_project(project_id: str, request: Optional[Request] = None, *, write: bool = False):
    """The project, 404 when there is none, 403 when the caller may not see
    its workspace or, for a ``write``, is not an ``editor`` there (one that
    names none is the default's). These paths name a project, not a
    workspace, so the middleware has nothing to check them against; the same
    rule ``routes/projects/_common.require_project_visible`` applies to the
    project's own routes."""
    project = _store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    access.require_visible(identity.request_principal(request), project.workspace)
    if write:
        record_access.require_in_workspace(request, project.workspace)
    return project


@router.get("/projects/{project_id}")
async def get_project_tracker(project_id: str, request: Request):
    project = _get_project(project_id, request)
    return project.tracker.model_dump()


@router.put("/projects/{project_id}")
async def update_project_tracker(project_id: str, payload: TrackerConfigUpdate, request: Request):
    _get_project(project_id, request, write=True)
    provider = (payload.provider or "none").strip().lower()
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown tracker provider: {payload.provider!r}")
    tracker = {"provider": provider, "remote_id": payload.remote_id, "url": payload.url}
    project = _store.update(project_id, tracker=tracker)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.tracker.model_dump()


@router.post("/projects/{project_id}/sync")
async def sync_project_tracker(project_id: str, request: Request):
    project = _get_project(project_id, request, write=True)
    provider = str(project.tracker.provider or "none")
    if provider not in ("jira", "linear"):
        raise HTTPException(status_code=400, detail="Project has no tracker configured")
    try:
        return await asyncio.to_thread(sync_tracker_issues, project)
    except (TrackerError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def tracker_status(workspace: Optional[str] = None):
    """Which trackers are connected for ``?workspace=``: whether the
    connector in effect there (its own, else the default workspace's) has the
    credentials a client needs. Building the client says so without a call to
    the tracker; whether the credentials still work is the Connectors page's
    connection test."""
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace or "") or "default"
    status = {}
    for provider in ("jira", "linear"):
        try:
            await asyncio.to_thread(get_provider, provider, ws)
            status[provider] = {"configured": True}
        except TrackerError:
            status[provider] = {"configured": False}
    return status


@router.get("/{provider}/projects")
async def list_tracker_projects(provider: str, workspace: Optional[str] = None):
    provider = provider.strip().lower()
    if provider not in ("jira", "linear"):
        raise HTTPException(status_code=400, detail=f"Unknown tracker provider: {provider!r}")
    from common.workspace_context import normalize_workspace_name
    try:
        client = get_provider(provider, normalize_workspace_name(workspace or "") or "default")
        return await asyncio.to_thread(client.list_remotes)
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e))
