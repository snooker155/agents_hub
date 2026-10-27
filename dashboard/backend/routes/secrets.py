"""
Workspace secrets over REST (common/secrets.py; docs/secrets.md).

Values go in and never come out: the list answers names, scopes and a four
character hint, and there is no route that reads a value back. A secret is
only ever read by the launcher, into the environment of a run whose agent
declares its name.

The agent side, ``/api/agents/{agent_id}/secrets``, edits which names an
agent may receive. It lives here rather than in routes/agents.py because it
is the other half of the same decision.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import identity
from common import secrets as secret_store

router = APIRouter(tags=["secrets"])


class SecretPut(BaseModel):
    value: str
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    # Hosts the value may be sent to (common/secrets.py "Secrets bound to
    # hosts"). None keeps what a replaced secret had; [] clears it.
    allowed_hosts: Optional[List[str]] = None


class SecretHostsPut(BaseModel):
    allowed_hosts: List[str]
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


@router.get("/api/workspaces/{name}/secrets")
async def list_workspace_secrets(name: str) -> List[dict]:
    _require_workspace(name)
    return secret_store.list_secrets(name)


@router.put("/api/workspaces/{name}/secrets/{secret}")
async def put_workspace_secret(request: Request, name: str, secret: str, payload: SecretPut):
    principal = _principal(request)
    _require_workspace(name)
    agent_id = (payload.agent_id or "").strip() or None
    user_id = (payload.user_id or "").strip() or None
    _check_scope(agent_id, user_id)
    try:
        row = secret_store.set_secret(name, secret, payload.value, agent_id=agent_id,
                                      user_id=user_id,
                                      created_by=getattr(principal, "id", None),
                                      allowed_hosts=payload.allowed_hosts)
    except secret_store.SecretsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return row


@router.put("/api/workspaces/{name}/secrets/{secret}/hosts")
async def put_workspace_secret_hosts(name: str, secret: str,
                                     payload: SecretHostsPut):
    """Change where a secret may be sent without re-entering its value."""
    _require_workspace(name)
    agent_id = (payload.agent_id or "").strip() or None
    user_id = (payload.user_id or "").strip() or None
    try:
        row = secret_store.set_secret_hosts(name, secret, payload.allowed_hosts,
                                            agent_id=agent_id, user_id=user_id)
    except secret_store.SecretsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return row


@router.delete("/api/workspaces/{name}/secrets/{secret}")
async def delete_workspace_secret(name: str, secret: str,
                                  agent_id: Optional[str] = None,
                                  user_id: Optional[str] = None):
    _require_workspace(name)
    if not secret_store.delete_secret(name, secret, agent_id=agent_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="No such secret")
    return {"deleted": True}


# ── the agent's allowlist ────────────────────────────────────────────────────

def _agent_or_404(agent_id: str):
    from agents.registry import get_agent
    spec = get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return spec


def _with_guard(spec) -> dict:
    """The declared names, and what the capability guard makes of them: the
    combination a saved record still forms (``capability_warning``, shown as a
    banner even when the save went through in warn mode or on the agent's own
    override), the guard's mode and whether this agent is exempted."""
    from agents.capability_guard import guard_mode
    from routes.agents.capabilities import _capability_warning_dict
    return {
        "secrets": list(spec.secrets or []),
        "capability_warning": _capability_warning_dict(spec),
        "guard_mode": guard_mode(),
        "capability_override": bool(getattr(spec, "capability_override", False)),
    }


@router.get("/api/agents/{agent_id}/secrets")
async def get_agent_secrets(agent_id: str) -> dict:
    return _with_guard(_agent_or_404(agent_id))


@router.put("/api/agents/{agent_id}/secrets")
async def put_agent_secrets(request: Request, agent_id: str, payload: AgentSecretsPut) -> dict:
    """Replace the names this agent may receive. Saved through
    ``registry.add_agent``, so the capability guard judges the new list.

    A system agent may declare secrets too: it is one record serving every
    workspace, so an operator declares a name once and stores the value in
    the default workspace (common/secrets.py falls back to it). The save is
    not a user edit of the agent: the seed does not own ``secrets``, so the
    agent keeps receiving the shipped updates of its tools and prompt."""
    from agents import registry
    principal = _principal(request)
    spec = _agent_or_404(agent_id)
    names: List[str] = []
    try:
        for raw in payload.secrets:
            name = secret_store.validate_name(raw)
            if name not in names:
                names.append(name)
    except secret_store.SecretsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        registry.add_agent(dataclasses.replace(spec, secrets=names), user_edit=False,
                           actor=getattr(principal, "username", None) or "dashboard",
                           note="secrets allowlist")
    except ValueError as exc:  # CapabilityViolation included
        raise HTTPException(status_code=400, detail=str(exc))
    return _with_guard(_agent_or_404(agent_id))
