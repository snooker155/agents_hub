"""
Agent-related API routes.
"""
from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pathlib import Path

from agents import registry, run_manager
from agents.agent_factory import get_factory
from agents import prompt_assembly
from tools.registry import get_all_tools
from models import AgentCreateCustom, AgentMemoryUpdate, AgentToolsUpdate, AgentModelUpdate, AgentReasoningUpdate, AgentSkillsConfigUpdate, AgentSkillCreate, AgentSharingUpdate
from workspace import SYSTEM_AGENT_IDS, get_workspace_metadata, update_workspace_metadata, create_workspace_folder


router = APIRouter(prefix="/api/agents", tags=["agents"])


def _workspace_for_chat_default(workspace: Optional[str]) -> str:
    return (workspace or "default").strip() or "default"


def _get_workspace_default_chat_agent(workspace: Optional[str]) -> Optional[str]:
    metadata = get_workspace_metadata(_workspace_for_chat_default(workspace))
    value = metadata.get("default_chat_agent")
    return str(value).strip() if value else None


def _set_workspace_default_chat_agent(workspace: Optional[str], agent_id: Optional[str]) -> None:
    ws_name = _workspace_for_chat_default(workspace)
    create_workspace_folder(ws_name)
    update_workspace_metadata(ws_name, {"default_chat_agent": agent_id or None})


def _agent_visible_in_workspace(agent: dict, workspace: Optional[str]) -> bool:
    """Return True if the agent should be visible in the given workspace.

    Rules:
    - System agents are visible everywhere.
    - Shared agents (exposed across workspaces) are visible everywhere.
    - An agent owned by a workspace is visible only in that workspace, unless
      it is shared.
    - Agents with no owner_workspace (legacy / globally available) are visible
      everywhere.
    """
    if agent.get("system") or agent.get("id") in SYSTEM_AGENT_IDS:
        return True
    if agent.get("shared"):
        return True
    owner = agent.get("owner_workspace")
    if not owner:
        return True
    return owner == (workspace or "default")


@router.get("")
async def list_agents(workspace: Optional[str] = None):
    """List all available agents from registry and factory definitions."""
    # Get agents from existing registry
    registry_agents = registry.list_agents()
    reg_ids = {a.id for a in registry_agents}

    # Get agent definitions from factory
    factory = get_factory()
    factory_agents = factory.list_available_agents()

    # Combine into single array for backward compatibility with frontend
    # Registry agents come first, then factory agents (if not already in registry)
    all_agents = [a.to_dict() for a in registry_agents]
    for fa in factory_agents:
        if fa["id"] not in reg_ids:
            all_agents.append(fa)

    # Filter by workspace
    from workspace import SYSTEM_AGENT_IDS as _SYS_IDS

    # Workspace-ownership visibility: a workspace-owned agent that is not shared
    # only appears in its owning workspace. Applies to every workspace,
    # including 'default'. System agents are always visible.
    all_agents = [a for a in all_agents if _agent_visible_in_workspace(a, workspace)]

    if workspace and workspace != "default":
        from workspace import get_workspace_metadata
        metadata = get_workspace_metadata(workspace)
        allowed = metadata.get("allowed_agents")
        if allowed is not None:
            all_agents = [
                a for a in all_agents
                if a["id"] in allowed
                or a["id"] in _SYS_IDS
                # An agent owned by this workspace is always available here even
                # if it was never explicitly added to allowed_agents.
                or a.get("owner_workspace") == workspace
            ]
        # Always hide default-workspace-only agents from non-default workspaces
        # (system agents are never default_workspace_only).
        all_agents = [a for a in all_agents if not a.get("default_workspace_only", False)]

    # Annotate each agent with whether it has a running node in the requested workspace
    # and flag system agents that cannot be removed.
    from agents.node_manager import get_running_nodes_for_agent
    for agent in all_agents:
        running_nodes = get_running_nodes_for_agent(agent["id"])
        if workspace and workspace != "default":
            running_nodes = [n for n in running_nodes if n.get("workspace") == workspace]
        agent["has_running_node"] = len(running_nodes) > 0
        agent["system"] = agent["id"] in _SYS_IDS
        agent["is_default_chat_agent"] = agent["id"] == _get_workspace_default_chat_agent(workspace)

    return all_agents


@router.get("/tools")
async def list_tools():
    """List all available tools. Returns factory, swe, and all for backward compatibility."""
    # Get tools from centralized registry
    all_registry_tools = get_all_tools()
    
    # Convert registry tools to dict format expected by frontend
    def tool_spec_to_dict(spec):
        return {
            "name": spec.id,
            "description": spec.description,
            "args": {p["name"]: p["type"] for p in spec.parameters}
        }
    
    registry_tools_dicts = [tool_spec_to_dict(t) for t in all_registry_tools]
    
    # For backward compatibility, return in the format the frontend expects
    return {
        "factory": registry_tools_dicts,  # All tools from registry
        "swe": registry_tools_dicts,      # Same tools (for compatibility)
        "all": registry_tools_dicts,      # Combined list
    }


@router.get("/{agent_id}")
async def get_agent_details(agent_id: str, workspace: Optional[str] = None):
    from workspace import is_system_agent
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    data = spec.to_dict()
    data["system"] = is_system_agent(agent_id)
    data["is_default_chat_agent"] = agent_id == _get_workspace_default_chat_agent(workspace)
    return data


@router.get("/{agent_id}/definition")
async def get_agent_definition(agent_id: str):
    """Return the agent's definition: structured fields + the markdown sources.

    The system prompt no longer lives in JSON — it is sourced from
    ``agents/definitions/<agent_id>/instructions.md`` (with optional
    ``capabilities.md`` and ``usage.md``).
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    factory = get_factory()
    defs_dir = factory.definitions_dir
    folder = prompt_assembly.agent_dir(agent_id, definitions_dir=defs_dir)

    instructions = prompt_assembly.read_instructions(agent_id, definitions_dir=defs_dir)
    capabilities = prompt_assembly.read_capabilities(agent_id, definitions_dir=defs_dir)
    usage = prompt_assembly.read_usage(agent_id, definitions_dir=defs_dir)

    # Assembled prompt only when instructions exist
    assembled = ""
    if instructions:
        assembled = prompt_assembly.assemble_prompt(agent_id, definitions_dir=defs_dir)

    return {
        "agent_id": spec.id,
        "source": "markdown" if instructions else "missing",
        "definition_dir": str(folder),
        "system_prompt": assembled,
        "instructions": instructions,
        "capabilities": capabilities,
        "usage": usage,
    }


class AgentInstructionsUpdate(__import__("pydantic").BaseModel):
    instructions: Optional[str] = None
    capabilities: Optional[str] = None
    usage: Optional[str] = None


@router.put("/{agent_id}/definition")
async def update_agent_definition(agent_id: str, data: AgentInstructionsUpdate):
    """Update the markdown sources for an agent's prompt.

    Pass any subset of {instructions, capabilities, usage}. Values that are
    None are left untouched; an empty string deletes that file.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    factory = get_factory()
    defs_dir = factory.definitions_dir

    if data.instructions is not None:
        if data.instructions.strip():
            prompt_assembly.write_instructions(agent_id, data.instructions, definitions_dir=defs_dir)
        else:
            raise HTTPException(status_code=400, detail="instructions.md cannot be empty")
    if data.capabilities is not None:
        path = prompt_assembly.agent_dir(agent_id, defs_dir) / prompt_assembly.CAPABILITIES_FILE
        if data.capabilities.strip():
            prompt_assembly.write_capabilities(agent_id, data.capabilities, definitions_dir=defs_dir)
        elif path.exists():
            path.unlink()
    if data.usage is not None:
        path = prompt_assembly.agent_dir(agent_id, defs_dir) / prompt_assembly.USAGE_FILE
        if data.usage.strip():
            prompt_assembly.write_usage(agent_id, data.usage, definitions_dir=defs_dir)
        elif path.exists():
            path.unlink()

    return await get_agent_definition(agent_id)


@router.get("/{agent_id}/workspace-capacities")
async def get_agent_workspace_capacities(agent_id: str):
    """Return workspace-specific capacity overrides for this agent (excludes 'default')."""
    from workspace import list_workspace_folders, get_workspace_metadata
    result = {}
    for ws_path in list_workspace_folders():
        ws_name = ws_path.name
        if ws_name == "default":
            continue
        meta = get_workspace_metadata(ws_name)
        overrides = meta.get("agent_capacity_overrides", {})
        if agent_id in overrides:
            result[ws_name] = overrides[agent_id]
    return result


@router.get("/{agent_id}/history")
async def get_agent_history(agent_id: str):
    runs = run_manager.load_runs()
    agent_runs = [r for r in runs if r.get("agent_id") == agent_id]
    # Sort by started_at desc
    agent_runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return agent_runs


@router.get("/{agent_id}/logs")
async def get_agent_logs(agent_id: str, node_id: Optional[str] = None, limit: int = 100):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    from agents import node_manager

    runs = run_manager.load_runs()
    agent_runs = [r for r in runs if r.get("agent_id") == agent_id]
    if node_id:
        agent_runs = [r for r in agent_runs if str(r.get("node_id") or "") == str(node_id)]
    agent_runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    if limit > 0:
        agent_runs = agent_runs[: max(1, min(int(limit), 500))]

    for r in agent_runs:
        lp = r.get("log_file")
        r["log_exists"] = bool(lp and Path(lp).exists())

    nodes = [n for n in node_manager.list_nodes() if n.get("agent_id") == agent_id]
    if node_id:
        nodes = [n for n in nodes if str(n.get("node_id") or "") == str(node_id)]
    nodes.sort(key=lambda n: n.get("started_at") or "", reverse=True)
    for n in nodes:
        lp = n.get("log_file")
        n["log_exists"] = bool(lp and Path(lp).exists())

    return {
        "agent_id": agent_id,
        "runs": agent_runs,
        "nodes": nodes,
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent: remove the JSON entry and the markdown folder."""
    from workspace import is_system_agent
    if is_system_agent(agent_id):
        raise HTTPException(
            status_code=403,
            detail=f"Agent '{agent_id}' is a system agent and cannot be deleted.",
        )
    found = registry.remove_agent(agent_id)
    if not found:
        raise HTTPException(status_code=404, detail="Agent not found")
    factory = get_factory()
    prompt_assembly.delete_definition(agent_id, definitions_dir=factory.definitions_dir)
    return {"message": f"Agent {agent_id} deleted"}


@router.post("/{agent_id}/memory")
async def update_agent_memory(agent_id: str, data: AgentMemoryUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=spec.tools,
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type=data.memory_type,
        memory_data=data.memory_data,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=spec.shared,
        provider=spec.provider,
        model=spec.model,
        base_url=spec.base_url,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        api_key=spec.api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.delete("/{agent_id}/memory")
async def erase_agent_memory(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=spec.tools,
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type="none",
        memory_data=None,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=spec.shared,
        provider=spec.provider,
        model=spec.model,
        base_url=spec.base_url,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        api_key=spec.api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.post("/{agent_id}/skills-config")
async def update_agent_skills_config(agent_id: str, data: AgentSkillsConfigUpdate):
    """Enable or disable procedural skills for this agent."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=spec.tools,
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
        skills_enabled=data.skills_enabled,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=spec.shared,
        provider=spec.provider,
        model=spec.model,
        base_url=spec.base_url,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        api_key=spec.api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.get("/{agent_id}/skills")
async def list_agent_skills(agent_id: str, workspace: str):
    """List all skills for this agent in the given workspace."""
    from memory.procedural import ProcedureStore
    store = ProcedureStore(workspace)
    procedures = [p for p in store.load() if p.agent_id == agent_id]
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "steps": p.steps,
            "tags": p.tags,
            "source": p.source,
            "success_rate": p.success_rate,
            "use_count": p.use_count,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
        }
        for p in procedures
    ]


@router.post("/{agent_id}/skills")
async def create_agent_skill(agent_id: str, data: AgentSkillCreate):
    """Create a new user-authored skill for this agent+workspace."""
    from memory.procedural import Procedure, ProcedureStore
    store = ProcedureStore(data.workspace)
    target = data.name.strip().lower()
    for p in store.load():
        if p.agent_id == agent_id and p.name.strip().lower() == target:
            raise HTTPException(
                status_code=400,
                detail=f"A skill named {data.name!r} already exists for this agent.",
            )
    procedure = Procedure(
        name=data.name,
        description=data.description,
        steps=data.steps,
        tags=data.tags,
        source="user",
        agent_id=agent_id,
        workspace=data.workspace,
    )
    store.add(procedure)
    return {
        "id": str(procedure.id),
        "name": procedure.name,
        "description": procedure.description,
        "steps": procedure.steps,
        "tags": procedure.tags,
        "source": procedure.source,
        "use_count": procedure.use_count,
        "created_at": procedure.created_at.isoformat(),
        "updated_at": procedure.updated_at.isoformat(),
    }


@router.delete("/{agent_id}/skills/{skill_id}")
async def delete_agent_skill(agent_id: str, skill_id: str, workspace: str):
    """Delete a skill. Only the owning agent can delete it."""
    from memory.procedural import ProcedureStore
    store = ProcedureStore(workspace)
    procedure = store.get(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    if procedure.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Skill belongs to a different agent")
    store.delete(skill_id)
    return {"ok": True}


@router.post("/{agent_id}/tools")
async def update_agent_tools(agent_id: str, data: AgentToolsUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=list(data.tools),
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=spec.shared,
        provider=spec.provider,
        model=spec.model,
        base_url=spec.base_url,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        api_key=spec.api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
        reasoning=spec.reasoning,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.get("/{agent_id}/reasoning")
async def get_agent_reasoning(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    r = spec.reasoning or {}
    tools = set(spec.tools or [])
    return {
        "think_enabled": bool(r.get("think_enabled", "think" in tools)),
        "think_mode": r.get("think_mode", "standard"),
        "plan_enabled": bool(r.get("plan_enabled", "plan" in tools)),
        "plan_format": r.get("plan_format", "structured"),
    }


@router.post("/{agent_id}/reasoning")
async def update_agent_reasoning(agent_id: str, data: AgentReasoningUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    current = dict(spec.reasoning or {})
    if data.think_enabled is not None:
        current["think_enabled"] = data.think_enabled
    if data.think_mode is not None:
        current["think_mode"] = data.think_mode
    if data.plan_enabled is not None:
        current["plan_enabled"] = data.plan_enabled
    if data.plan_format is not None:
        current["plan_format"] = data.plan_format

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=spec.tools,
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=spec.shared,
        provider=spec.provider,
        model=spec.model,
        base_url=spec.base_url,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        api_key=spec.api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
        http_expose=spec.http_expose,
        http_port=spec.http_port,
        http_host_port=spec.http_host_port,
        node_type=spec.node_type,
        is_default_chat_agent=spec.is_default_chat_agent,
        skills_enabled=spec.skills_enabled,
        reasoning=current,
    )
    registry.add_agent(new_spec)
    _tools = set(spec.tools or [])
    return {
        "think_enabled": bool(current.get("think_enabled", "think" in _tools)),
        "think_mode": current.get("think_mode", "standard"),
        "plan_enabled": bool(current.get("plan_enabled", "plan" in _tools)),
        "plan_format": current.get("plan_format", "structured"),
    }


@router.get("/{agent_id}/model")
async def get_agent_model(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "provider":    spec.provider or "inherit",
        "model":       spec.model or "",
        "base_url":    spec.base_url or "",
        "temperature": spec.temperature,   # None means "inherit global"
        "max_tokens":  spec.max_tokens,    # None means "inherit global"
        "has_api_key": bool(spec.api_key), # never expose the key value
    }


@router.post("/{agent_id}/model")
async def update_agent_model(agent_id: str, data: AgentModelUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Provider → top-level field
    new_provider = spec.provider
    if data.provider is not None:
        new_provider = None if data.provider == "inherit" else data.provider

    # When switching to global/inherit, clear model and base_url too
    if new_provider is None:
        new_model = None
        new_base_url = None
    else:
        # Model name → top-level field
        new_model = spec.model
        if data.model is not None:
            new_model = data.model.strip() or None

        # Base URL → top-level field
        new_base_url = spec.base_url
        if data.base_url is not None:
            new_base_url = data.base_url.strip() or None

    # API key override — stored as flat field (sensitive; not exposed in to_dict)
    new_api_key = spec.api_key
    if data.clear_api_key:
        new_api_key = None
    elif data.api_key is not None and data.api_key.strip():
        new_api_key = data.api_key.strip()

    # Temperature override — stored as flat field
    new_temperature = spec.temperature
    if data.clear_temperature:
        new_temperature = None
    elif data.temperature is not None:
        new_temperature = data.temperature

    # Max tokens override — stored as flat field
    new_max_tokens = spec.max_tokens
    if data.clear_max_tokens:
        new_max_tokens = None
    elif data.max_tokens is not None:
        new_max_tokens = data.max_tokens

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=spec.tools,
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=spec.shared,
        provider=new_provider,
        model=new_model,
        base_url=new_base_url,
        temperature=new_temperature,
        max_tokens=new_max_tokens,
        api_key=new_api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
    )
    registry.add_agent(new_spec)
    return {
        "provider":    new_provider or "inherit",
        "model":       new_model or "",
        "base_url":    new_base_url or "",
        "temperature": new_temperature,
        "max_tokens":  new_max_tokens,
        "has_api_key": bool(new_api_key),
    }


@router.get("/{agent_id}/health")
async def health_agent(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "up", "type": "local"}


@router.post("/{agent_id}/set-default-chat")
async def set_default_chat_agent(agent_id: str, workspace: Optional[str] = None):
    """Set this agent as the workspace default pre-selected agent in the Chat page."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    ws_name = _workspace_for_chat_default(workspace)
    metadata = get_workspace_metadata(ws_name)
    allowed = metadata.get("allowed_agents")
    if allowed is not None and agent_id not in allowed and agent_id not in SYSTEM_AGENT_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent_id}' is not available in workspace '{ws_name}'",
        )

    _set_workspace_default_chat_agent(ws_name, agent_id)
    return {"workspace": ws_name, "default_chat_agent": agent_id}


@router.delete("/{agent_id}/set-default-chat")
async def clear_default_chat_agent(agent_id: str, workspace: Optional[str] = None):
    """Clear this workspace's default chat agent when it matches this agent."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    ws_name = _workspace_for_chat_default(workspace)
    if _get_workspace_default_chat_agent(ws_name) == agent_id:
        _set_workspace_default_chat_agent(ws_name, None)
    return {"workspace": ws_name, "default_chat_agent": _get_workspace_default_chat_agent(ws_name)}


@router.post("/create")
async def create_custom_agent(data: AgentCreateCustom):
    """Create a new agent: register structured fields and write instructions.md."""
    if registry.get_agent(data.id) is not None:
        raise HTTPException(status_code=400, detail=f"Agent '{data.id}' already exists")

    if not data.system_prompt or not data.system_prompt.strip():
        raise HTTPException(status_code=400, detail="system_prompt is required")

    factory = get_factory()
    prompt_assembly.write_instructions(
        data.id, data.system_prompt, definitions_dir=factory.definitions_dir
    )

    # Agents created inside a (non-default) workspace are owned by — and only
    # visible in — that workspace until they are explicitly shared.
    owner_workspace = (data.workspace or "").strip() or None
    if owner_workspace == "default":
        owner_workspace = None

    spec = registry.AgentSpec(
        id=data.id,
        name=data.name,
        description=data.description,
        domain=data.domain,
        type="langchain",
        entrypoint="agents.agent_factory:build_agent_executor",
        tools=data.tools,
        capacity=data.capacity,
        owner_workspace=owner_workspace,
    )
    try:
        registry.add_agent(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Register the new agent in its owning workspace's allowed_agents so it shows
    # up immediately in that workspace's UI.
    if owner_workspace:
        try:
            create_workspace_folder(owner_workspace)
            metadata = get_workspace_metadata(owner_workspace)
            allowed = list(metadata.get("allowed_agents") or [])
            if data.id not in allowed:
                allowed.append(data.id)
                update_workspace_metadata(owner_workspace, {"allowed_agents": allowed})
        except Exception:
            # Best-effort; the agent itself is created and owner_workspace is set.
            pass

    return spec.to_dict()


@router.post("/{agent_id}/sharing")
async def update_agent_sharing(agent_id: str, data: AgentSharingUpdate):
    """Expose or un-expose an agent across workspaces.

    When ``shared`` is True the agent becomes visible in (and addable to) every
    workspace. When False it reverts to being visible only in its owning
    workspace (``owner_workspace``).
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        tools=spec.tools,
        commands=spec.commands,
        capacity=spec.capacity,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
        default_workspace_only=spec.default_workspace_only,
        owner_workspace=spec.owner_workspace,
        shared=data.shared,
        provider=spec.provider,
        model=spec.model,
        base_url=spec.base_url,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        api_key=spec.api_key,
        verbose=spec.verbose,
        streaming=spec.streaming,
        http_expose=spec.http_expose,
        http_port=spec.http_port,
        http_host_port=spec.http_host_port,
        node_type=spec.node_type,
        is_default_chat_agent=spec.is_default_chat_agent,
        skills_enabled=spec.skills_enabled,
        reasoning=spec.reasoning,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()
