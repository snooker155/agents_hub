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

from fastapi import APIRouter, HTTPException

from chat import references

router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("/kinds")
async def list_kinds():
    """The attach menu's entity kinds: ``[{kind, noun, icon, workspace_scoped}]``."""
    return references.kind_catalog()


@router.get("/{kind}")
async def list_kind_entities(
    kind: str,
    workspace: Optional[str] = None,
    project_id: Optional[str] = None,
    q: str = "",
    limit: int = references.LIST_LIMIT,
):
    """Candidates of one kind for the picker, newest/most relevant first.

    ``q`` is a plain substring match over the entity's name, description and id —
    the picker filters server-side so a large workspace does not ship its whole
    task list to the browser on every keystroke.
    """
    if kind not in references.KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown context entity kind '{kind}'")
    return references.list_entities(
        kind, workspace=workspace, project_id=project_id, query=q, limit=limit,
    )


@router.get("/{kind}/{entity_id}/preview")
async def preview_entity(kind: str, entity_id: str):
    """The exact text this reference contributes to the prompt."""
    if kind not in references.KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown context entity kind '{kind}'")
    rendered = references.render_entity(kind, entity_id)
    if rendered is None:
        raise HTTPException(status_code=404, detail=f"{kind} '{entity_id}' not found")
    return {"kind": kind, "id": entity_id, **rendered}
