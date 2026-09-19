"""
Marketplace API routes.

The marketplace is the catalog of things published across workspaces: agents
(``AgentSpec.shared``), flows (``flow.shared``) and skills
(``Procedure.shared``). A workspace-owned item becomes visible here once it is
published from its own page; any workspace can then take it — agents via
``POST /api/workspaces/{name}/agents``, flows via ``POST /api/workspaces/{name}/flows``,
skills via ``POST /api/skills/{skill_id}/install``.

These routes intentionally expose only definition-level information about an
agent (prompt markdown, tools, commands, skills, model) — never user- or
workspace-related data such as sessions, memories, runs or tasks.
"""
from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional

from agents import registry, prompt_assembly
from agents.agent_factory import get_factory
from flow import store as flow_store
from tools.registry import get_all_tools
from workspace import is_system_agent, get_workspace_metadata

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


def _is_marketplace_agent(spec: registry.AgentSpec) -> bool:
    """An agent is listed on the marketplace when explicitly published."""
    if is_system_agent(spec.id):
        return False
    if spec.default_workspace_only:
        return False
    return bool(spec.shared)


def _agent_in_workspace(spec: registry.AgentSpec, workspace: Optional[str]) -> bool:
    """Whether the agent is already available in the given workspace."""
    ws = (workspace or "default").strip() or "default"
    if ws == "default":
        # Shared agents are always visible in the default workspace.
        return True
    if spec.owner_workspace == ws:
        return True
    metadata = get_workspace_metadata(ws)
    allowed = metadata.get("allowed_agents")
    if allowed is None:
        # No allow-list configured: shared agents are visible everywhere.
        return True
    return spec.id in allowed


def _model_info(spec: registry.AgentSpec) -> Dict[str, Any]:
    """Public model configuration — never exposes the API key or base URL."""
    return {
        "provider": spec.provider or "inherit",
        "model": spec.model or "",
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
    }


@router.get("/agents")
async def list_marketplace_agents(workspace: Optional[str] = None):
    """List all agents published to the marketplace.

    Pass ``workspace`` to annotate each card with ``in_workspace`` — whether
    the agent is already available in that workspace.
    """
    items: List[Dict[str, Any]] = []
    for spec in registry.list_agents():
        if not _is_marketplace_agent(spec):
            continue
        items.append({
            "id": spec.id,
            "name": spec.name,
            "description": spec.description,
            "domain": spec.domain,
            "type": spec.type,
            "owner_workspace": spec.owner_workspace,
            "tools": list(spec.tools or []),
            "commands_count": len(spec.commands or []),
            "skills_enabled": spec.skills_enabled,
            "model": _model_info(spec),
            "in_workspace": _agent_in_workspace(spec, workspace),
        })
    items.sort(key=lambda a: a["name"].lower())
    return items


def _flow_in_workspace(
    flow: Dict[str, Any], workspace: Optional[str], all_flows: List[Dict[str, Any]]
) -> bool:
    """Whether the flow (or a copy of it) is already available in the workspace.

    Flows without a workspace binding are visible everywhere; a marketplace
    flow added to a workspace is cloned there with ``origin_flow_id`` pointing
    back at the published flow.
    """
    ws = (workspace or "default").strip() or "default"
    owner = flow.get("workspace")
    if not owner or owner == ws:
        return True
    return any(
        f.get("workspace") == ws and f.get("origin_flow_id") == flow.get("id")
        for f in all_flows
    )


@router.get("/flows")
async def list_marketplace_flows(workspace: Optional[str] = None):
    """List all flows published to the marketplace, with the agents they use.

    Pass ``workspace`` to annotate each card with ``in_workspace`` — whether
    the flow is already available in that workspace.
    """
    all_flows = flow_store.list_flows()
    items: List[Dict[str, Any]] = []
    for flow in all_flows:
        if not flow.get("shared"):
            continue
        agents: List[Dict[str, Any]] = []
        for agent_id in flow_store.flow_agent_ids(flow):
            spec = registry.get_agent(agent_id)
            agents.append({
                "id": agent_id,
                "name": spec.name if spec else agent_id,
                "domain": spec.domain if spec else "",
                "system": is_system_agent(agent_id),
                "on_marketplace": bool(spec and _is_marketplace_agent(spec)),
            })
        items.append({
            "id": flow.get("id"),
            "name": flow.get("name") or flow.get("id"),
            "description": flow.get("description", ""),
            "owner_workspace": flow.get("workspace"),
            "nodes_count": len(flow.get("nodes") or []),
            "agents": agents,
            "updated_at": flow.get("updated_at"),
            "in_workspace": _flow_in_workspace(flow, workspace, all_flows),
        })
    items.sort(key=lambda f: (f["name"] or "").lower())
    return items


@router.get("/skills")
async def list_marketplace_skills(workspace: Optional[str] = None):
    """List every skill published to the global catalog.

    A skill is a named procedure ("when to use this" plus ordered steps) that
    any workspace can install a copy of and attach to its own agents — see
    ``routes/skills.py``. Pass ``workspace`` to annotate each card with
    ``in_workspace``: whether that workspace already owns a copy.
    """
    from memory.procedural import all_procedures

    procedures = all_procedures()
    ws = (workspace or "").strip()
    # A workspace "has" a published skill when it owns the original or any copy
    # descended from it (origin_skill_id chains to the published id).
    local_origins = {
        str(p.origin_skill_id) for p in procedures
        if ws and p.workspace == ws and p.origin_skill_id
    }
    local_ids = {str(p.id) for p in procedures if ws and p.workspace == ws}

    items: List[Dict[str, Any]] = []
    for p in procedures:
        if not p.shared:
            continue
        attached = registry.get_agent(p.agent_id) if p.agent_id else None
        items.append({
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "steps_count": len(p.steps),
            "steps": list(p.steps),
            "tags": list(p.tags),
            "source": p.source,
            "owner_workspace": p.workspace,
            "authored_for": (
                {"id": p.agent_id, "name": attached.name if attached else p.agent_id}
                if p.agent_id else None
            ),
            "use_count": p.use_count,
            "updated_at": p.updated_at.isoformat(),
            "in_workspace": bool(ws) and (str(p.id) in local_ids or str(p.id) in local_origins),
        })
    items.sort(key=lambda s: (s["name"] or "").lower())
    return items


@router.get("/agents/{agent_id}")
async def get_marketplace_agent(agent_id: str, workspace: Optional[str] = None):
    """Public details for a marketplace agent: definition, tools, skills,
    model and commands. No sessions, memories, runs or tasks."""
    spec = registry.get_agent(agent_id)
    if not spec or not _is_marketplace_agent(spec):
        raise HTTPException(status_code=404, detail="Agent not found on the marketplace")

    # Definition markdown (shared definition folder when definition_id is set)
    defs_dir = get_factory().definitions_dir
    def_id = spec.def_id()
    instructions = prompt_assembly.read_instructions(def_id, definitions_dir=defs_dir)
    capabilities = prompt_assembly.read_capabilities(def_id, definitions_dir=defs_dir)
    usage = prompt_assembly.read_usage(def_id, definitions_dir=defs_dir)

    # Tool metadata from the central registry; unknown ids fall back to bare entries
    tool_meta = {t.id: t for t in get_all_tools()}
    tools = []
    for tid in spec.tools or []:
        t = tool_meta.get(tid)
        tools.append({
            "id": tid,
            "name": t.name if t else tid,
            "category": t.category if t else "other",
            "description": t.description if t else "",
        })

    # Skills authored in the agent's owning workspace (definition-level knowledge)
    skills: List[Dict[str, Any]] = []
    try:
        from memory.procedural import ProcedureStore
        store = ProcedureStore(spec.owner_workspace or "default")
        skills = [
            {
                "id": str(p.id),
                "name": p.name,
                "description": p.description,
                "steps": p.steps,
                "tags": p.tags,
                "source": p.source,
            }
            for p in store.load()
            if p.agent_id == agent_id
        ]
    except Exception:
        skills = []

    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "domain": spec.domain,
        "type": spec.type,
        "owner_workspace": spec.owner_workspace,
        "skills_enabled": spec.skills_enabled,
        "definition": {
            "instructions": instructions,
            "capabilities": capabilities,
            "usage": usage,
        },
        "tools": tools,
        "commands": list(spec.commands or []),
        "skills": skills,
        "model": _model_info(spec),
        "reasoning": dict(spec.reasoning or {}),
        "in_workspace": _agent_in_workspace(spec, workspace),
    }
