"""
Context reference API — what the chat composer can attach besides a file.

The composer's attach menu needs two things: the list of attachable entity kinds
and, per kind, the candidates in the current workspace/project. Both come from
the one catalog in ``chat.references``, so the picker can never drift from what
the prompt builder actually knows how to render.

``/preview`` exists for the chip's "what exactly gets sent?" popover — it returns
the same text the agent will receive, so the user is never guessing.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from chat import references
from common import access, identity

router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("/kinds")
async def list_kinds():
    """The attach menu's entity kinds: ``[{kind, noun, icon, workspace_scoped}]``."""
    return references.kind_catalog()


def _visible(principal, row: dict) -> bool:
    """Whether the caller may see one row or rendered record. A record with
    no workspace field at all (an agent) answers to its own rules; a shared
    flow is everyone's; any other that names no workspace is
    default's (common/access.py)."""
    if "workspace" not in row or row.get("shared"):
        return True
    return access.can_see_workspace(principal, row.get("workspace"))


@router.get("/{kind}")
async def list_kind_entities(
    request: Request,
    kind: str,
    workspace: Optional[str] = None,
    project_id: Optional[str] = None,
    q: str = "",
    limit: int = references.LIST_LIMIT,
):
    """Candidates of one kind for the picker, newest/most relevant first.

    ``q`` is a plain substring match over the entity's name, description and id —
    the picker filters server-side so a large workspace does not ship its whole
    task list to the browser on every keystroke. Only the records the caller
    may see.
    """
    if kind not in references.KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown context entity kind '{kind}'")
    principal = identity.request_principal(request)
    if access.visible_workspaces(principal) is None:
        return references.list_entities(
            kind, workspace=workspace, project_id=project_id, query=q, limit=limit,
        )
    rows = references.list_entities(
        kind, workspace=workspace, project_id=project_id, query=q, limit=10_000,
    )
    return [r for r in rows if _visible(principal, r)][:max(1, int(limit))]


@router.get("/{kind}/{entity_id}/preview")
async def preview_entity(kind: str, entity_id: str, request: Request):
    """The exact text this reference contributes to the prompt."""
    if kind not in references.KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown context entity kind '{kind}'")
    rendered = references.render_entity(kind, entity_id)
    if rendered is None:
        raise HTTPException(status_code=404, detail=f"{kind} '{entity_id}' not found")
    principal = identity.request_principal(request)
    if kind != "agent" and access.visible_workspaces(principal) is not None:
        shared = kind == "flow" and bool((_flow(entity_id) or {}).get("shared"))
        if not shared:
            access.require_visible(principal, rendered.get("workspace"))
    return {"kind": kind, "id": entity_id, **rendered}


def _flow(flow_id: str):
    from flow.store import get_flow
    return get_flow(flow_id)
