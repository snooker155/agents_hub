"""Flow templates over REST (``flow/templates/``, docs/flow-templates.md).

A template is a ready flow of system agents, each step with its own task,
plus example requests in every interface language.

- ``GET /api/flow-templates?lang=``: every template with its steps, the
  agents it runs and its examples in ``lang``.
- ``POST /api/flow-templates/{id}/create``: creates a flow from it. ``name``
  and ``description`` may be overridden (the dialog sends them in the
  interface language); step labels follow ``lang``. ``workspace`` binds the
  flow there, as the editor's workspace picker does; without it the flow is
  shared, like a blank flow from the Create flow dialog. Requires the
  editor role in that workspace (default when none).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, identity
from common.auth import WS_EDITOR
from flow import store as flow_store
from flow import templates

router = APIRouter(prefix="/api/flow-templates", tags=["flow_templates"])


class CreateFromTemplate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workspace: Optional[str] = None
    lang: str = "en"


def _template_or_404(template_id: str) -> templates.FlowTemplate:
    try:
        template = templates.get_template(template_id)
    except templates.TemplateError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if template is None:
        raise HTTPException(status_code=404, detail=f"Unknown flow template '{template_id}'")
    return template


@router.get("")
async def list_flow_templates(lang: str = "en"):
    return [t.to_dict(lang) for t in templates.list_templates()]


@router.get("/{template_id}")
async def get_flow_template(template_id: str, lang: str = "en"):
    return _template_or_404(template_id).to_dict(lang)


@router.post("/{template_id}/create")
async def create_from_template(template_id: str, data: CreateFromTemplate, request: Request):
    template = _template_or_404(template_id)
    workspace = (data.workspace or "").strip() or None
    principal = identity.request_principal(request)
    if workspace:
        access.require_visible(principal, workspace)
    identity.require_role(principal, workspace=workspace or "default", role=WS_EDITOR)

    missing = templates.ensure_agents(template)
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"This hub has no agent {', '.join(missing)} that the template runs",
        )
    flow = templates.build_flow(template, str(uuid4()), lang=data.lang)
    flow["name"] = (data.name or "").strip() or template.name
    flow["description"] = (data.description or "").strip() or template.description
    flow["workspace"] = workspace
    flow["task_id"] = None
    now = datetime.now(timezone.utc).isoformat()
    flow["created_at"] = now
    flow["updated_at"] = now
    flow_store.save_flow(flow)
    if workspace:
        from routes.flows import _authorize_flow_in_workspace
        _authorize_flow_in_workspace(workspace, flow["id"])
    return flow
