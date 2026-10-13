"""
Routes: a workspace's isolation (common/isolation.py, docs/isolation.md).

- ``GET /api/workspaces/{name}/isolation``: the switch, the domains the
  workspace may read from, the hub's readiness checks, the agents it owns
  that hold tools it would not allow, and the allowlist itself.
- ``PUT /api/workspaces/{name}/isolation`` with ``{"isolated": bool,
  "allow_domains": [...]}`` (each key optional). Turning isolation on is
  refused (409) until every readiness check passes, no owned agent holds a
  tool from outside the allowlist, and nothing leads around the perimeter
  (MCP servers, chat bindings, widgets). Turning it off needs only the role.

Both need the workspace's owner or an administrator: ``isolation`` is an
owner scoped segment (common/auth.py), and the routes check again.

- ``GET/PUT /api/workspaces/{name}/data-class`` with ``{"data_class":
  "public" | "internal" | "confidential"}``: how sensitive the workspace's
  work is, which decides the linked nodes that may see it
  (common/data_class.py, docs/hub-link.md). Same owner rule.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import audit, data_class, identity, isolation
from common.auth import WS_OWNER

router = APIRouter(prefix="/api/workspaces", tags=["isolation"])


class IsolationPut(BaseModel):
    isolated: Optional[bool] = None
    allow_domains: Optional[List[str]] = None


def _require(request: Request, name: str):
    principal = identity.request_principal(request)
    identity.require_role(principal, workspace=name, role=WS_OWNER)
    from workspace import get_workspace_folder
    if not get_workspace_folder(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' does not exist")
    return principal


@router.get("/{name}/isolation")
def get_isolation(name: str, request: Request):
    _require(request, name)
    return isolation.state(name)


@router.put("/{name}/isolation")
def put_isolation(name: str, body: IsolationPut, request: Request):
    principal = _require(request, name)
    before = isolation.is_isolated(name)
    try:
        out = isolation.update(name, isolated=body.isolated, allow_domains_=body.allow_domains,
                               actor=getattr(principal, "username", None) or getattr(principal, "id", None))
    except isolation.IsolationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    audit.record("workspace.isolation", principal=principal, object_type="workspace", object_id=name,
                 workspace=name, ip=identity.client_ip(request),
                 details={"isolated": out["isolated"], "was": before,
                          "allow_domains": out["allow_domains"]})
    return out


class DataClassPut(BaseModel):
    data_class: str


def _data_class_state(name: str) -> dict:
    return {"data_class": data_class.workspace_class(name), "classes": list(data_class.CLASSES),
            "default": data_class.DEFAULT_CLASS, "required_trust": dict(data_class.REQUIRED_TRUST)}


@router.get("/{name}/data-class")
def get_data_class(name: str, request: Request):
    _require(request, name)
    return _data_class_state(name)


@router.put("/{name}/data-class")
def put_data_class(name: str, body: DataClassPut, request: Request):
    principal = _require(request, name)
    before = data_class.workspace_class(name)
    try:
        data_class.set_workspace_class(name, body.data_class,
                                       actor=getattr(principal, "username", None) or getattr(principal, "id", None))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = _data_class_state(name)
    audit.record("workspace.data_class", principal=principal, object_type="workspace", object_id=name,
                 workspace=name, ip=identity.client_ip(request),
                 details={"data_class": out["data_class"], "was": before})
    return out
