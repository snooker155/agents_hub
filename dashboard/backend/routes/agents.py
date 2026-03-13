"""
Agent-related API routes.
"""
from fastapi import APIRouter, HTTPException
from typing import List, Optional
import yaml
from pathlib import Path

from agents import registry, run_manager
from agents.factory import get_factory
from tools.registry import get_all_tools
from models import AgentClone, AgentConnect, AgentCreateCustom, AgentMemoryUpdate, AgentToolsUpdate, AgentModelUpdate, YamlManifest


router = APIRouter(prefix="/api/agents", tags=["agents"])


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

    # Filter by workspace's allowed_agents if workspace is specified
    if workspace:
        from common.workspace import get_workspace_metadata
        metadata = get_workspace_metadata(workspace)
        allowed = metadata.get("allowed_agents")
        if allowed is not None:
            all_agents = [a for a in all_agents if a["id"] in allowed]

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
async def get_agent_details(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return spec.to_dict()


@router.get("/{agent_id}/definition")
async def get_agent_definition(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    factory = get_factory()
    yaml_path = Path(factory.definitions_dir) / f"{agent_id}.yaml"
    yaml_text = None
    source = "generated-from-registry"
    system_prompt = None

    if yaml_path.exists():
        try:
            yaml_text = yaml_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(yaml_text) or {}
            if isinstance(parsed, dict):
                system_prompt = parsed.get("system_prompt")
            source = "yaml-file"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read YAML definition: {e}")

    if not system_prompt:
        dp = spec.default_params if isinstance(spec.default_params, dict) else {}
        system_prompt = dp.get("system_prompt")

    if not yaml_text:
        generated = {
            "kind": "Agent",
            "metadata": {"name": spec.id},
            "spec": {
                "displayName": spec.name,
                "description": spec.description,
                "domain": spec.domain,
                "type": spec.type,
                "entrypoint": spec.entrypoint,
                "capacity": spec.capacity,
                "isRemote": spec.is_remote,
                "agentUrl": spec.agent_url,
                "tools": spec.tools,
                "defaultParams": spec.default_params,
                "memoryType": spec.memory_type,
                "memoryData": spec.memory_data,
            },
        }
        yaml_text = yaml.safe_dump(generated, sort_keys=False, allow_unicode=True)

    return {
        "agent_id": spec.id,
        "source": source,
        "yaml_path": str(yaml_path) if yaml_path.exists() else None,
        "system_prompt": system_prompt or "",
        "yaml": yaml_text,
    }


@router.get("/{agent_id}/history")
async def get_agent_history(agent_id: str):
    runs = run_manager._load_runs()
    agent_runs = [r for r in runs if r.get("agent_id") == agent_id]
    # Sort by started_at desc
    agent_runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return agent_runs


@router.post("/clone")
async def clone_agent(clone: AgentClone):
    original = registry.get_agent(clone.original_id)
    if not original:
        raise HTTPException(status_code=404, detail="Original agent not found")

    new_spec = registry.AgentSpec(
        id=clone.new_id,
        name=clone.new_name,
        type=original.type,
        entrypoint=original.entrypoint,
        default_params=original.default_params,
        tools=original.tools,
        capacity=original.capacity,
        is_remote=original.is_remote,
        agent_url=original.agent_url,
        original_id=original.id
    )
    try:
        registry.add_agent(new_spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return new_spec.to_dict()


@router.post("/connect")
async def connect_agent(data: AgentConnect):
    spec = registry.AgentSpec(
        id=data.id,
        name=data.name,
        description=data.description,
        domain=data.domain,
        type="http",
        entrypoint="remote", # dummy for remote
        capacity=data.capacity,
        is_remote=True,
        agent_url=data.agent_url,
        tools=data.tools
    )
    try:
        registry.add_agent(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return spec.to_dict()


@router.post("/{agent_id}/disconnect")
async def disconnect_agent(agent_id: str):
    found = registry.remove_agent(agent_id)
    if not found:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"message": f"Agent {agent_id} disconnected"}


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
        default_params=spec.default_params,
        tools=spec.tools,
        capacity=spec.capacity,
        is_remote=spec.is_remote,
        agent_url=spec.agent_url,
        original_id=spec.original_id,
        memory_type=data.memory_type,
        memory_data=data.memory_data
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
        default_params=spec.default_params,
        tools=spec.tools,
        capacity=spec.capacity,
        is_remote=spec.is_remote,
        agent_url=spec.agent_url,
        original_id=spec.original_id,
        memory_type="none",
        memory_data=None
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.post("/{agent_id}/tools")
async def update_agent_tools(agent_id: str, data: AgentToolsUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    default_params = dict(spec.default_params or {})
    if data.tools:
        default_params["tools"] = list(data.tools)
    else:
        default_params.pop("tools", None)

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        default_params=default_params,
        tools=list(data.tools),
        capacity=spec.capacity,
        is_remote=spec.is_remote,
        agent_url=spec.agent_url,
        original_id=spec.original_id,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.get("/{agent_id}/model")
async def get_agent_model(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    dp = dict(spec.default_params or {})
    return {
        "provider":    dp.get("provider", "inherit"),
        "model":       dp.get("model", ""),
        "base_url":    dp.get("base_url", ""),
        "temperature": dp.get("temperature"),   # None means "inherit global"
        "max_tokens":  dp.get("max_tokens"),    # None means "inherit global"
        "has_api_key": bool(dp.get("api_key")), # never expose the key value
    }


@router.post("/{agent_id}/model")
async def update_agent_model(agent_id: str, data: AgentModelUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    dp = dict(spec.default_params or {})

    # Provider
    if data.provider is not None:
        if data.provider == "inherit":
            dp.pop("provider", None)
        else:
            dp["provider"] = data.provider

    # Model name
    if data.model is not None:
        if data.model.strip():
            dp["model"] = data.model.strip()
        else:
            dp.pop("model", None)

    # API key override (only store if non-empty; clear_api_key removes it)
    if data.clear_api_key:
        dp.pop("api_key", None)
    elif data.api_key is not None and data.api_key.strip():
        dp["api_key"] = data.api_key.strip()

    # Base URL override
    if data.base_url is not None:
        if data.base_url.strip():
            dp["base_url"] = data.base_url.strip()
        else:
            dp.pop("base_url", None)

    # Temperature override
    if data.clear_temperature:
        dp.pop("temperature", None)
    elif data.temperature is not None:
        dp["temperature"] = data.temperature

    # Max tokens override
    if data.clear_max_tokens:
        dp.pop("max_tokens", None)
    elif data.max_tokens is not None:
        dp["max_tokens"] = data.max_tokens

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        description=spec.description,
        domain=spec.domain,
        default_params=dp,
        tools=spec.tools,
        capacity=spec.capacity,
        is_remote=spec.is_remote,
        agent_url=spec.agent_url,
        original_id=spec.original_id,
        memory_type=spec.memory_type,
        memory_data=spec.memory_data,
    )
    registry.add_agent(new_spec)
    return {
        "provider":    dp.get("provider", "inherit"),
        "model":       dp.get("model", ""),
        "base_url":    dp.get("base_url", ""),
        "temperature": dp.get("temperature"),
        "max_tokens":  dp.get("max_tokens"),
        "has_api_key": bool(dp.get("api_key")),
    }


@router.get("/{agent_id}/health")
async def health_agent(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not spec.is_remote or not spec.agent_url:
        return {"status": "up", "type": "local"}

    from agents.remote_runner import check_health
    return check_health(spec.agent_url)


@router.post("/apply")
async def apply_agent_manifest(data: YamlManifest):
    try:
        manifest = yaml.safe_load(data.yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not manifest or manifest.get("kind") != "Agent":
        raise HTTPException(status_code=400, detail="Manifest must have 'kind: Agent'")

    metadata = manifest.get("metadata", {})
    spec_data = manifest.get("spec", {})

    agent_id = metadata.get("name")
    if not agent_id:
        raise HTTPException(status_code=400, detail="Manifest must have metadata.name")

    # Build AgentSpec
    spec = registry.AgentSpec(
        id=agent_id,
        name=spec_data.get("displayName", agent_id),
        description=spec_data.get("description", ""),
        domain=spec_data.get("domain", "general"),
        type=spec_data.get("type", "langchain"),
        entrypoint=spec_data.get("entrypoint", "swe_agent.agent:build_agent"),
        capacity=spec_data.get("capacity", 1),
        is_remote=spec_data.get("isRemote", False),
        agent_url=spec_data.get("agentUrl"),
        tools=spec_data.get("tools", spec_data.get("capabilities", [])),
        default_params=spec_data.get("defaultParams", {})
    )

    try:
        registry.add_agent(spec)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return spec.to_dict()


@router.post("/create")
async def create_custom_agent(data: AgentCreateCustom):
    # Base it on swe-fs but with custom system prompt and tools
    base = registry.get_agent("swe-fs")
    if not base:
        # Fallback if swe-fs is missing
        entrypoint = "swe_agent.agent:build_agent"
    else:
        entrypoint = base.entrypoint

    spec = registry.AgentSpec(
        id=data.id,
        name=data.name,
        description=data.description,
        domain=data.domain,
        type="langchain",
        entrypoint=entrypoint,
        default_params={"system_prompt": data.system_prompt, "tools": data.tools},
        tools=data.tools,
        capacity=data.capacity
    )
    try:
        registry.add_agent(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return spec.to_dict()
