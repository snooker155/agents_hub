"""
Flow Entity Registry API routes.

Exposes the federated catalog of flow-usable entities (agents, processors,
conditions, transforms, ...) backing the dashboard "Registry" menu. See
``flow/registry.py`` for the entity model and discovery rules.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flow import registry as flow_registry

router = APIRouter(prefix="/api/flow-entities", tags=["flow-entities"])


class FlowEntityCreate(BaseModel):
    id: str
    name: str
    category: str
    entrypoint: str
    description: str = ""
    group: str = ""
    inputs: List[str] = []
    outputs: List[str] = []
    config_schema: Dict[str, Any] = {}
    icon: str = ""


@router.get("")
async def list_flow_entities(category: Optional[str] = None, workspace: Optional[str] = None):
    """Return entities grouped by category for the Registry menu.

    With ?category=, returns the flat list for just that category. With
    ?workspace=, the agent category is scoped to that workspace by ownership
    (other categories are workspace-independent).
    """
    groups = flow_registry.list_groups(workspace=workspace)
    if category:
        return [s.to_dict() for s in groups.get(category, [])]
    return {cat: [s.to_dict() for s in specs] for cat, specs in groups.items()}


@router.get("/{entity_id}")
async def get_flow_entity(entity_id: str):
    spec = flow_registry.get_entity(entity_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Flow entity not found")
    return spec.to_dict()


@router.post("")
async def create_flow_entity(data: FlowEntityCreate):
    """Create or update a user/data-defined entity (persisted to flow_entities.json)."""
    try:
        spec = flow_registry.FlowEntitySpec(
            id=data.id,
            name=data.name,
            category=data.category,
            entrypoint=data.entrypoint,
            description=data.description,
            group=data.group,
            inputs=data.inputs,
            outputs=data.outputs,
            config_schema=data.config_schema,
            icon=data.icon,
            source="user",
        )
        flow_registry.add_user_entity(spec)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return spec.to_dict()


@router.delete("/{entity_id}")
async def delete_flow_entity(entity_id: str):
    """Delete a user-defined entity. Agents and builtins cannot be removed here."""
    spec = flow_registry.get_entity(entity_id)
    if spec and spec.source != "user":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete a '{spec.source}' entity from the registry",
        )
    if not flow_registry.remove_user_entity(entity_id):
        raise HTTPException(status_code=404, detail="Flow entity not found")
    return {"message": "Flow entity deleted"}
