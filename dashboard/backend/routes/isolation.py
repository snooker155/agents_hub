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
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import audit, identity, isolation
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
