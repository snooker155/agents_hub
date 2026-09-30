"""Project deployments: the API under ``/api/projects/{id}/deployment`` and
the published apps under ``/apps/<slug>/``. See docs/project-deployments.md.

The API is the project page's Deploy tab and the agent tools' counterpart:
configure the services, deploy, stop, restart, rebuild, read logs and the
journal, and manage the share link. A deploy runs off the event loop (it
builds images) and answers as soon as it starts; the page polls the record.

``/apps/<slug>/`` is outside ``/api`` and therefore an open path
(common/auth.py's ``is_open_path``): the credential is the deployment's own
share key, presented once as ``?key=`` and kept in a cookie scoped to that
slug's path, or nothing at all when the deployment is ``public``. The page is
proxied by the same code the preview iframe uses (``routes.preview.proxy_to``),
so a redirect, an absolute link or a form action on the app stays under
``/apps/<slug>/``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

from common import access, identity
from common.auth import LOCAL_OPERATOR_ID, WS_EDITOR
from deployments import service as deployments, store as dstore
from deployments.models import ProjectDeployment
from routes.preview import proxy_to

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["project-deployments"])
list_router = APIRouter(prefix="/api/deployments", tags=["project-deployments"])
apps_router = APIRouter(prefix="/apps", tags=["project-deployments"])

COOKIE_PREFIX = "ah_app_"


# ── helpers ──────────────────────────────────────────────────────────────────

def _raise(exc: deployments.DeploymentError) -> None:
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _principal(request: Request):
    return identity.request_principal(request)


def _principal_id(request: Request) -> str:
    principal = _principal(request)
    return principal.id if principal else LOCAL_OPERATOR_ID


def _load(request: Request, project_id: str, *, create: bool = True,
          write: bool = False) -> ProjectDeployment:
    try:
        project = deployments.require_project(project_id)
        dep = deployments.for_project(project_id, create=create)
    except deployments.DeploymentError as exc:
        _raise(exc)
    principal = _principal(request)
    access.require_visible(principal, project.workspace)
    if write:
        identity.require_role(principal, workspace=project.workspace, role=WS_EDITOR)
    if dep is None:
        raise HTTPException(status_code=404, detail="The project has no deployment")
    return dep


def _describe(dep: ProjectDeployment) -> Dict[str, Any]:
    return deployments.describe(dep)


# ── the API ──────────────────────────────────────────────────────────────────

class DeploymentPatch(BaseModel):
    name: Optional[str] = None
    mode: Optional[str] = None
    compose_file: Optional[str] = None
    services: Optional[List[Dict[str, Any]]] = None
    environment_id: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    primary_service: Optional[str] = None
    restart_on_exit: Optional[bool] = None


class VisibilityBody(BaseModel):
    visibility: str


@router.get("/{project_id}/deployment")
async def get_deployment(project_id: str, request: Request, refresh: bool = True):
    """The project's deployment, proposed from its folder on first read.
    ``refresh`` (default on) asks the runner about every service first."""
    dep = _load(request, project_id)
    if refresh and dep.desired == "running":
        dep = await asyncio.to_thread(deployments.refresh, dep)
    return _describe(dep)


@router.put("/{project_id}/deployment")
async def update_deployment(project_id: str, payload: DeploymentPatch, request: Request):
    _load(request, project_id, write=True)
    raw = payload.model_dump()
    patch = {k: v for k, v in raw.items() if v is not None}
    # Clearing an optional field: the client sends "" for it.
    for key in ("compose_file", "environment_id", "primary_service"):
        if raw.get(key) == "":
            patch[key] = None
    try:
        dep = deployments.update_config(project_id, patch)
    except deployments.DeploymentError as exc:
        _raise(exc)
    return _describe(dep)


@router.post("/{project_id}/deployment/detect")
async def detect_deployment(project_id: str, request: Request):
    _load(request, project_id, write=True)
    try:
        dep = await asyncio.to_thread(deployments.redetect, project_id)
    except deployments.DeploymentError as exc:
        _raise(exc)
    return _describe(dep)


def _start_in_background(dep: ProjectDeployment, *, by: str, build: bool) -> None:
    async def _run() -> None:
        try:
            await asyncio.to_thread(deployments.deploy, dep, by=by, build=build)
        except deployments.DeploymentError as exc:
            log.info("deployment %s: %s", dep.id, exc)
        except Exception:  # noqa: BLE001 - journaled on the record, never lost in a task
            log.exception("deployment %s: deploy failed", dep.id)

            def mark(d: ProjectDeployment) -> None:
                d.status = "failed"
                d.desired = "stopped"
                d.add_event("deploy_failed", "unexpected error, see the backend log")
            dstore.mutate(dep.id, mark)
    asyncio.create_task(_run())


@router.post("/{project_id}/deployment/deploy", status_code=202)
async def deploy_project(project_id: str, request: Request, build: bool = True):
    """Start (or restart, rebuilding images) every service. Answers at once;
    the record's ``status`` moves through building and starting."""
    dep = _load(request, project_id, write=True)
    if not dep.services:
        raise HTTPException(status_code=422, detail="The deployment has no services: detect or add some first")
    if deployments.in_flight(dep.id):
        raise HTTPException(status_code=409, detail="A deploy or stop of this project is already in progress")

    def mark(d: ProjectDeployment) -> None:
        d.status = "building" if build else "starting"
        d.desired = "running"
    dstore.mutate(dep.id, mark)
    _start_in_background(dep, by=_principal_id(request), build=build)
    return _describe(dstore.get(dep.id) or dep)


@router.post("/{project_id}/deployment/restart", status_code=202)
async def restart_project(project_id: str, request: Request):
    return await deploy_project(project_id, request, build=False)


@router.post("/{project_id}/deployment/stop")
async def stop_project(project_id: str, request: Request):
    dep = _load(request, project_id, write=True)
    try:
        dep = await asyncio.to_thread(deployments.stop, dep, by=_principal_id(request))
    except deployments.DeploymentError as exc:
        _raise(exc)
    return _describe(dep)


@router.delete("/{project_id}/deployment")
async def remove_deployment(project_id: str, request: Request):
    dep = _load(request, project_id, create=False, write=True)
    await asyncio.to_thread(deployments.remove, dep)
    return {"ok": True}


@router.get("/{project_id}/deployment/logs")
async def deployment_logs(project_id: str, request: Request, service: Optional[str] = None,
                          tail: int = 200):
    dep = _load(request, project_id)
    tail = max(1, min(int(tail), 2000))
    if service == "build":
        return {"service": "build", "text": deployments.build_log(dep, tail)}
    try:
        return await asyncio.to_thread(deployments.logs, dep, service, tail)
    except deployments.DeploymentError as exc:
        _raise(exc)


@router.get("/{project_id}/deployment/events")
async def deployment_events(project_id: str, request: Request, limit: int = 100):
    dep = _load(request, project_id)
    events = [e.model_dump(mode="json") for e in dep.events]
    return {"items": list(reversed(events))[: max(1, min(int(limit), 500))]}


@router.put("/{project_id}/deployment/visibility")
async def set_visibility(project_id: str, payload: VisibilityBody, request: Request):
    dep = _load(request, project_id, write=True)
    try:
        dep = deployments.set_visibility(dep, payload.visibility)
    except deployments.DeploymentError as exc:
        _raise(exc)
    return _describe(dep)


@router.post("/{project_id}/deployment/link/reset")
async def reset_link(project_id: str, request: Request):
    dep = _load(request, project_id, write=True)
    return _describe(deployments.rotate_link(dep))


@list_router.get("/apps")
async def list_apps(request: Request, workspace: Optional[str] = None):
    """Every project deployment the caller may see, for the Deployments page."""
    principal = _principal(request)
    items = []
    for dep in dstore.list_all(workspace=workspace):
        if not access.can_see_workspace(principal, dep.workspace):
            continue
        items.append(deployments.describe(dep, with_token=False))
    return {"items": items}


# ── /apps/<slug>/ ────────────────────────────────────────────────────────────

_GONE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>App not running</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:48px;color:#555">
<p>This application is not running right now.</p>
<p>Deploy it from the hub and reload.</p>
</body></html>
"""

_KEY_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Link required</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:48px;color:#555">
<p>This application is private. Open it with the share link from the hub.</p>
</body></html>
"""


def _cookie_name(slug: str) -> str:
    return f"{COOKIE_PREFIX}{slug.replace('-', '_')}"


def _authorized(dep: ProjectDeployment, request: Request) -> bool:
    if dep.visibility == "public":
        return True
    presented = request.query_params.get("key") or request.cookies.get(_cookie_name(dep.slug))
    return deployments.share_key_matches(dep, presented)


@apps_router.api_route("/{slug}", methods=["GET", "HEAD"])
async def app_root_redirect(slug: str, request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/apps/{slug}/{query}", status_code=307)


@apps_router.api_route(
    "/{slug}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def app_proxy(slug: str, path: str, request: Request):
    dep = dstore.by_slug(slug)
    if dep is None:
        raise HTTPException(status_code=404, detail="No such app")
    if not _authorized(dep, request):
        return HTMLResponse(_KEY_HTML, status_code=403)
    # A service other than the primary: /apps/<slug>/~<service>/<path>.
    service_name: Optional[str] = None
    if path.startswith("~"):
        head, _, rest = path.partition("/")
        service_name, path = head[1:], rest
    raw_base = deployments.target_url(dep, service_name)
    if not raw_base:
        return HTMLResponse(_GONE_HTML, status_code=503)
    prefix = f"/apps/{slug}/" + (f"~{service_name}/" if service_name else "")
    key = request.query_params.get("key")
    if key and deployments.share_key_matches(dep, key):
        # The key came on the URL: remember it in a cookie for this app's
        # path and drop it from what the app sees.
        stripped = "&".join(part for part in request.url.query.split("&") if not part.startswith("key="))
        target = f"{prefix}{path}" + (f"?{stripped}" if stripped else "")
        resp: Response = RedirectResponse(url=target, status_code=307)
        resp.set_cookie(_cookie_name(slug), key, path=f"/apps/{slug}", httponly=True,
                        samesite="lax", max_age=30 * 24 * 3600)
        return resp
    return await proxy_to(raw_base, path, request, prefix=prefix)


__all__ = ["router", "list_router", "apps_router"]
