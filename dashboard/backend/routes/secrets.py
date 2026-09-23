"""
Workspace secrets over REST (common/secrets.py; docs/secrets.md).

Values go in and never come out: the list answers names, scopes and a four
character hint, and there is no route that reads a value back. A secret is
only ever read by the launcher, into the environment of a run whose agent
declares its name.

Who may touch them: ``secrets`` is an owner-scoped workspace segment
(``common.auth.OWNER_SCOPED_SEGMENTS``), so in ``multi`` mode the middleware
already refuses anyone but the workspace's owner or an administrator, reads
included. The routes check again with ``identity.require_role`` so the rule
does not depend on the middleware's path parsing alone. These routes exist in
every mode: one operator on a laptop has keys to keep out of ``.env`` too.

The agent side, ``/api/agents/{agent_id}/secrets``, edits which names an
agent may receive. It lives here rather than in routes/agents.py because it
is the other half of the same decision, and it is stricter than an ordinary
agent edit: in ``multi`` mode only an administrator, or the owner of the
workspace the agent belongs to, may widen what an agent can read.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import audit, identity
from common import secrets as secret_store
from common.auth import MULTI, WS_OWNER

router = APIRouter(tags=["secrets"])


class SecretPut(BaseModel):
    value: str
    agent_id: Optional[str] = None
    user_id: Optional[str] = None


class AgentSecretsPut(BaseModel):
    secrets: List[str]


def _principal(request: Request):
    return identity.request_principal(request)


def _require_workspace(name: str) -> None:
    from workspace import get_workspace_folder
    if not get_workspace_folder(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' does not exist")


def _check_scope(agent_id: Optional[str], user_id: Optional[str]) -> None:
    """A scope must name something real, or the secret could never be handed out."""
    if agent_id:
        from agents.registry import get_agent
        if get_agent(agent_id) is None:
            raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_id}'")
    if user_id and identity.current_mode() == MULTI and identity.get_user(user_id) is None:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user_id}'")


def _scope_details(name: str, agent_id: Optional[str], user_id: Optional[str]) -> dict:
    # What the audit row carries: the name and the scope, never the value.
    return {"name": name, "agent_id": agent_id or "", "user_id": user_id or ""}


@router.get("/api/workspaces/{name}/secrets")
async def list_workspace_secrets(request: Request, name: str) -> List[dict]:
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    _require_workspace(name)
    return secret_store.list_secrets(name)


@router.put("/api/workspaces/{name}/secrets/{secret}")
async def put_workspace_secret(request: Request, name: str, secret: str, payload: SecretPut):
    principal = _principal(request)
    identity.require_role(principal, workspace=name, role=WS_OWNER)
    _require_workspace(name)
    agent_id = (payload.agent_id or "").strip() or None
    user_id = (payload.user_id or "").strip() or None
    _check_scope(agent_id, user_id)
    try:
        row = secret_store.set_secret(name, secret, payload.value, agent_id=agent_id,
                                      user_id=user_id,
                                      created_by=getattr(principal, "id", None))
    except secret_store.SecretsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("secret.set", principal=principal, object_type="secret", object_id=secret,
                 workspace=name, ip=identity.client_ip(request),
                 details=_scope_details(secret, agent_id, user_id))
    return row


@router.delete("/api/workspaces/{name}/secrets/{secret}")
async def delete_workspace_secret(request: Request, name: str, secret: str,
                                  agent_id: Optional[str] = None,
                                  user_id: Optional[str] = None):
    principal = _principal(request)
    identity.require_role(principal, workspace=name, role=WS_OWNER)
    _require_workspace(name)
    if not secret_store.delete_secret(name, secret, agent_id=agent_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="No such secret")
    audit.record("secret.delete", principal=principal, object_type="secret", object_id=secret,
                 workspace=name, ip=identity.client_ip(request),
                 details=_scope_details(secret, agent_id, user_id))
    return {"deleted": True}


# ── the agent's allowlist ────────────────────────────────────────────────────

def _agent_or_404(agent_id: str):
    from agents.registry import get_agent
    spec = get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return spec


def _require_agent_admin(principal, spec) -> None:
    """Administrator, or owner of the workspace the agent belongs to."""
    if identity.current_mode() != MULTI:
        return
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if principal.is_admin:
        return
    workspace = getattr(spec, "owner_workspace", None)
    if not workspace:
        raise HTTPException(status_code=403, detail="Administrator access required")
    identity.require_role(principal, workspace=workspace, role=WS_OWNER)


@router.get("/api/agents/{agent_id}/secrets")
async def get_agent_secrets(agent_id: str) -> dict:
    return {"secrets": list(_agent_or_404(agent_id).secrets or [])}


@router.put("/api/agents/{agent_id}/secrets")
async def put_agent_secrets(request: Request, agent_id: str, payload: AgentSecretsPut) -> dict:
    """Replace the names this agent may receive. Saved through
    ``registry.add_agent``, so the capability guard judges the new list."""
    from agents import registry
    principal = _principal(request)
    spec = _agent_or_404(agent_id)
    _require_agent_admin(principal, spec)
    names: List[str] = []
    try:
        for raw in payload.secrets:
            name = secret_store.validate_name(raw)
            if name not in names:
                names.append(name)
    except secret_store.SecretsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        registry.add_agent(dataclasses.replace(spec, secrets=names),
                           actor=getattr(principal, "username", None) or "dashboard",
                           note="secrets allowlist")
    except ValueError as exc:  # CapabilityViolation included
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("agent.secrets", principal=principal, object_type="agent", object_id=agent_id,
                 ip=identity.client_ip(request), details={"secrets": names})
    return {"secrets": names}
