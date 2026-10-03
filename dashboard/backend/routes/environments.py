"""Environments over REST (environments/, docs/environments.md).

An environment is an execution profile (mode, image, packages, network
policy, limits, plain variables) a task run, a resident instance or a
scheduled job runs in. These routes are the Environments page's backend and
the pickers on the agent page's Run and the Cluster page.

Who may do what. Reading follows workspace visibility: a list shows the
global environments plus those of workspaces the caller can see, a single
record is refused when its workspace is not visible. Writing a workspace's
environment needs the editor role in it; writing a global one (``workspace``
None, usable by every workspace) needs an administrator, since it changes
what runs in workspaces the caller may not belong to. Outside ``multi`` mode
every check is a no-op. Every write lands in the audit log as
``environment.<verb>``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity
from common.auth import WS_EDITOR
from environments import service
from environments.models import Environment

router = APIRouter(prefix="/api/environments", tags=["environments"])


class NetworkIn(BaseModel):
    type: Optional[str] = None
    allowed_hosts: Optional[List[str]] = None
    allow_package_managers: Optional[bool] = None


class LimitsIn(BaseModel):
    memory: Optional[str] = None
    cpus: Optional[str] = None
    pids_limit: Optional[int] = None


class EnvironmentCreate(BaseModel):
    name: str
    description: str = ""
    workspace: Optional[str] = None
    mode: str = "inherit"
    image: Optional[str] = None
    packages: List[str] = []
    network: Optional[NetworkIn] = None
    limits: Optional[LimitsIn] = None
    # Named sandbox preset (environments/models.py SIZE_PRESETS); validated by
    # the Environment model, so an unknown name is a 400 from the service.
    size: Optional[str] = None
    env: Dict[str, str] = {}
    is_default: bool = False
    sandbox_provider: str = "inherit"


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workspace: Optional[str] = None
    mode: Optional[str] = None
    image: Optional[str] = None
    packages: Optional[List[str]] = None
    network: Optional[NetworkIn] = None
    limits: Optional[LimitsIn] = None
    size: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    is_default: Optional[bool] = None
    sandbox_provider: Optional[str] = None


def _principal(request: Request):
    return identity.request_principal(request)


def _raise(exc: service.EnvironmentServiceError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _require_write(principal, workspace: Optional[str]) -> None:
    if workspace:
        identity.require_role(principal, workspace=workspace, role=WS_EDITOR)
    else:
        identity.require_role(principal, admin=True)


def _load_visible(request: Request, env_id: str) -> Environment:
    try:
        env = service.require_environment(env_id)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    access.require_visible(_principal(request), env.workspace)
    return env


def _record(request: Request, action: str, env: Environment, details: Optional[dict] = None) -> None:
    audit.record(action, principal=_principal(request), object_type="environment",
                 object_id=env.id, workspace=env.workspace, ip=identity.client_ip(request),
                 details={"name": env.name, **(details or {})})


def _dump(model: Optional[BaseModel]) -> Optional[dict]:
    return model.model_dump(exclude_unset=True) if model is not None else None


@router.get("")
async def list_environments(request: Request, workspace: Optional[str] = None,
                            include_archived: bool = False):
    """Global environments plus ``workspace``'s own (every scope the caller
    can see when no workspace is named), each with ``usage_counts``."""
    envs = service.list_environments(workspace, include_archived=include_archived,
                                     all_scopes=not workspace)
    rows = [service.to_dict(e, with_usage=True) for e in envs]
    return access.filter_by_workspace(_principal(request), rows)


@router.get("/resolve")
async def resolve_environment(request: Request, workspace: Optional[str] = None,
                              environment_id: Optional[str] = None):
    """The environment a new run in ``workspace`` would get (the explicit one,
    else the workspace default, else the global default), or null."""
    if workspace:
        access.require_visible(_principal(request), workspace)
    try:
        env = service.resolve_for(workspace, environment_id or None)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    return service.to_dict(env) if env is not None else None


@router.get("/sandbox/providers")
async def sandbox_providers():
    """Every sandbox/registry.py provider, whether it can run something right
    now and why not: the Environments page's provider picker and its
    availability hints."""
    from sandbox import registry
    return registry.available()


@router.post("")
async def create_environment(request: Request, payload: EnvironmentCreate):
    principal = _principal(request)
    data = payload.model_dump(exclude_unset=True)
    data["network"] = _dump(payload.network) or {}
    data["limits"] = _dump(payload.limits) or {}
    workspace = (payload.workspace or "").strip() or None
    _require_write(principal, workspace)
    try:
        env = service.create_environment(data)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    _record(request, "environment.create", env, {"mode": env.mode, "network": env.network.type})
    return service.to_dict(env, with_usage=True)


@router.get("/{env_id}")
async def get_environment(request: Request, env_id: str):
    return service.to_dict(_load_visible(request, env_id), with_usage=True)


@router.patch("/{env_id}")
async def update_environment(request: Request, env_id: str, payload: EnvironmentUpdate):
    principal = _principal(request)
    current = _load_visible(request, env_id)
    _require_write(principal, current.workspace)
    patch = payload.model_dump(exclude_unset=True)
    if payload.network is not None:
        patch["network"] = _dump(payload.network)
    if payload.limits is not None:
        patch["limits"] = _dump(payload.limits)
    if "workspace" in patch:
        target = (patch["workspace"] or "").strip() or None
        if target != current.workspace:
            _require_write(principal, target)
    try:
        env = service.update_environment(env_id, patch)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    _record(request, "environment.update", env, {"fields": sorted(patch)})
    return service.to_dict(env, with_usage=True)


@router.post("/{env_id}/archive")
async def archive_environment(request: Request, env_id: str):
    current = _load_visible(request, env_id)
    _require_write(_principal(request), current.workspace)
    try:
        env = service.archive_environment(env_id)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    _record(request, "environment.archive", env)
    return service.to_dict(env, with_usage=True)


@router.delete("/{env_id}")
async def delete_environment(request: Request, env_id: str):
    current = _load_visible(request, env_id)
    _require_write(_principal(request), current.workspace)
    try:
        service.delete_environment(env_id)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    _record(request, "environment.delete", current)
    return {"deleted": True}


@router.post("/{env_id}/default")
async def make_default(request: Request, env_id: str):
    current = _load_visible(request, env_id)
    _require_write(_principal(request), current.workspace)
    try:
        env = service.set_default(env_id)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    _record(request, "environment.update", env, {"fields": ["is_default"]})
    return service.to_dict(env, with_usage=True)


@router.get("/{env_id}/usage")
async def environment_usage(request: Request, env_id: str):
    """Nodes, scheduled jobs and the 50 most recent runs tied to it, each
    narrowed to the workspaces the caller can see."""
    _load_visible(request, env_id)
    try:
        data = service.usage(env_id)
    except service.EnvironmentServiceError as exc:
        _raise(exc)
    principal = _principal(request)
    return {key: access.filter_by_workspace(principal, rows) for key, rows in data.items()}


@router.post("/{env_id}/build")
async def build_environment_image(request: Request, env_id: str):
    """Build the derived image with the environment's packages now, instead of
    at the first run that needs it. Not for a local-mode environment (400).
    Answers ``{ok, image, error}``; with no packages, the base image as is."""
    env = _load_visible(request, env_id)
    _require_write(_principal(request), env.workspace)
    if env.mode == "local":
        raise HTTPException(status_code=400, detail="A local environment runs no container image")
    import asyncio

    from managers import container_manager as cm

    def _build() -> Dict[str, Any]:
        base = cm.environment_base_image(env.image)
        if not env.packages:
            return {"ok": True, "image": base, "error": None}
        result = cm.build_environment_image(base, list(env.packages))
        return {"ok": bool(result["ok"]), "image": result["image"], "error": result["error"]}

    try:
        result = await asyncio.to_thread(_build)
    except Exception as exc:  # noqa: BLE001 - no docker CLI, daemon down: reported, not raised
        result = {"ok": False, "image": None, "error": str(exc)}
    _record(request, "environment.build", env, {"ok": result["ok"], "image": result["image"]})
    return result
