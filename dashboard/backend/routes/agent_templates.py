"""Agent templates over REST (``agents/templates/``, docs/agent-templates.md).

A template is one ready agent to start from: a prompt, a tool set, the
connectors it works best with and a default outcome rubric.

- ``GET /api/agent-templates``: every template without its prompt, each
  connector's configured status for ``workspace`` and a free agent id.
- ``GET /api/agent-templates/{id}``: one template with its prompt.
- ``POST /api/agent-templates/{id}/create``: creates an agent from it in
  ``workspace``. Identity fields, the prompt and the tools may be overridden
  (the wizard lets the person adjust them); the rest comes from the template.
  Requires the editor role there. The agent is created through the same
  ``POST /api/agents/create`` handler the wizard's custom path uses, so the
  capability guard and workspace ownership rules apply unchanged, and the
  template's rubric is then set as the agent's default outcome.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import kits
from agents import registry
from agents import templates
from common import access, identity
from common.auth import WS_EDITOR
from models import AgentCreateCustom

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-templates", tags=["agent_templates"])


class CreateFromTemplate(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    workspace: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[List[str]] = None
    capacity: int = 1


def _template_or_404(template_id: str) -> templates.AgentTemplate:
    try:
        template = templates.get_template(template_id)
    except templates.TemplateError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if template is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent template '{template_id}'")
    return template


def _taken(agent_id: str) -> bool:
    return registry.get_agent(agent_id) is not None


def _payload(template: templates.AgentTemplate, workspace: Optional[str], *, with_prompt: bool) -> Dict[str, Any]:
    out = template.to_dict(with_prompt=with_prompt)
    ws = workspace or "default"
    out["connectors"] = {
        kind: [{"name": n, "configured": kits.connector_status(n, ws)} for n in names]
        for kind, names in (("required", template.connectors_required),
                            ("optional", template.connectors_optional))
    }
    out["suggested_id"] = templates.free_agent_id(template.id, _taken)
    return out


@router.get("")
async def list_agent_templates(workspace: Optional[str] = None):
    return [_payload(t, workspace, with_prompt=False) for t in templates.list_templates()]


@router.get("/{template_id}")
async def get_agent_template(template_id: str, workspace: Optional[str] = None):
    return _payload(_template_or_404(template_id), workspace, with_prompt=True)


@router.post("/{template_id}/create")
async def create_from_template(template_id: str, data: CreateFromTemplate, request: Request):
    template = _template_or_404(template_id)
    workspace = (data.workspace or "").strip() or None
    principal = identity.request_principal(request)
    if workspace:
        access.require_visible(principal, workspace)
    identity.require_role(principal, workspace=workspace or "default", role=WS_EDITOR)

    # The wizard's own id style: lowercase, no spaces.
    agent_id = re.sub(r"\s+", "_", (data.id or "").strip()).lower() \
        or templates.free_agent_id(template.id, _taken)
    prompt = data.system_prompt if (data.system_prompt or "").strip() else template.system_prompt
    body = AgentCreateCustom(
        id=agent_id,
        name=(data.name or "").strip() or template.name,
        description=(data.description or "").strip() or template.description,
        domain=template.domain,
        system_prompt=prompt,
        tools=list(data.tools) if data.tools is not None else list(template.tools),
        capacity=max(1, data.capacity),
        workspace=workspace,
    )
    from routes.agents.lifecycle import create_custom_agent
    created = await create_custom_agent(body, request)

    if template.outcome:
        from tasks.outcome import OutcomeError, normalize_outcome
        spec = registry.get_agent(agent_id)
        try:
            if spec is not None:
                registry.add_agent(dataclasses.replace(
                    spec, default_outcome=normalize_outcome(dict(template.outcome))))
                created = (registry.get_agent(agent_id) or spec).to_dict()
        except (OutcomeError, ValueError):
            # The agent exists either way; its rubric can be set on its Outcome card.
            log.warning("agent templates: could not set the outcome of %s", agent_id, exc_info=True)
    return {"agent": created, "template": template.id, "next_steps": template.next_steps}
