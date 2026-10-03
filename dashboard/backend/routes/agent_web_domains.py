"""
Routes: an agent's own domain lists for the web tools (tools/web.py).

``GET/PUT /api/agents/{agent_id}/web-domains`` reads and writes
``AgentSpec.allowed_domains`` and ``AgentSpec.blocked_domains``. The
workspace's lists live in its settings (``routes/workspaces.py``, the
web-policy route) and the global ones in .env (``routes/settings.py``); the
GET answers the merged picture too, so the agent card can show what a run of
this agent in a workspace will actually be held to. Saved like the other
per-field agent routes: ``dataclasses.replace`` plus ``registry.add_agent``,
which snapshots version history.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents import registry
from common import audit, identity

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent_web_domains"])


class WebDomainsUpdate(BaseModel):
    allowed_domains: Optional[List[str]] = None
    blocked_domains: Optional[List[str]] = None


def _payload(spec: Any, workspace: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "allowed_domains": list(getattr(spec, "allowed_domains", None) or []),
        "blocked_domains": list(getattr(spec, "blocked_domains", None) or []),
    }
    if workspace:
        from tools.web import effective_domain_lists
        try:
            out["effective"] = effective_domain_lists(spec, workspace)
        except Exception:  # noqa: BLE001 - the merged view is a courtesy, the lists stand without it
            log.debug("web domains: no merged view for %s in %s", spec.id, workspace, exc_info=True)
    return out


@router.get("/api/agents/{agent_id}/web-domains")
async def get_web_domains(agent_id: str, workspace: Optional[str] = None):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _payload(spec, workspace)


@router.put("/api/agents/{agent_id}/web-domains")
async def update_web_domains(agent_id: str, data: WebDomainsUpdate, request: Request):
    from tools.web import clean_host_list
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if getattr(spec, "system", False):
        raise HTTPException(status_code=403, detail="A system agent's lists cannot be edited")
    changes: Dict[str, Any] = {}
    try:
        if "allowed_domains" in data.model_fields_set:
            changes["allowed_domains"] = clean_host_list(data.allowed_domains or [])
        if "blocked_domains" in data.model_fields_set:
            changes["blocked_domains"] = clean_host_list(data.blocked_domains or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_spec = dataclasses.replace(spec, **changes) if changes else spec
    try:
        registry.add_agent(new_spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        audit.record("agent.web_domains", principal=identity.request_principal(request),
                     object_type="agent", object_id=agent_id,
                     workspace=getattr(new_spec, "owner_workspace", None),
                     ip=identity.client_ip(request), details=changes)
    except Exception:  # noqa: BLE001 - an audit failure must not undo the save
        log.warning("could not record the web-domains audit entry for %s", agent_id, exc_info=True)
    return _payload(new_spec, None)
