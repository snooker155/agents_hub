"""
Agent-related API routes.

Includes the agent's own definition chat (``/{agent_id}/definition/chat``),
where the Agent Creator edits an agent's instructions, capabilities and usage in
place while the user watches the files change beside the conversation.
"""
import json
import re
import dataclasses

from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
from pydantic import BaseModel

from agents import registry
from managers import run_manager
from agents.agent_factory import get_factory
from agents import prompt_assembly
from agents import versions as agent_versions
from tools.registry import get_all_tools
from agents.capability_guard import CapabilityViolation
from models import AgentCreateCustom, AgentCloneToWorkspace, AgentMemoryUpdate, AgentToolsUpdate, AgentDelegatesUpdate, AgentModelUpdate, AgentReasoningUpdate, AgentSkillsConfigUpdate, AgentEpisodicConfigUpdate, AgentResponseFormatUpdate, AgentClarifyGateUpdate, AgentSelfDelegationUpdate, AgentSkillCreate, AgentSharingUpdate, AgentDescriptionUpdate, AgentListItem, AgentDetail, AgentPage
from workspace import system_agent_ids, get_workspace_metadata, update_workspace_metadata, create_workspace_folder
from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router


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
    if agent.get("system") or agent.get("id") in system_agent_ids():
        return True
    if agent.get("shared"):
        return True
    owner = agent.get("owner_workspace")
    if not owner:
        return True
    return owner == (workspace or "default")


def _apply_workspace_memory(agent: dict, workspace: str, mem_overrides: dict) -> None:
    """Patch an agent dict's memory fields with the workspace-effective assignment.

    The record-level assignment applies only in the agent's home workspace
    (owner_workspace, or 'default'); elsewhere the workspace metadata override
    applies — absent means no shared memory there.
    """
    home = agent.get("owner_workspace") or "default"
    if workspace == home:
        return
    entry = mem_overrides.get(agent.get("id"))
    if isinstance(entry, dict):
        agent["memory_type"] = entry.get("memory_type") or "none"
        agent["memory_data"] = entry.get("memory_data")
    else:
        agent["memory_type"] = "none"
        agent["memory_data"] = None


def _workspace_memory_overrides(workspace: str) -> dict:
    return get_workspace_metadata(workspace).get("agent_memory_overrides") or {}


def _light_registry_dict(spec) -> Dict[str, Any]:
    """Just the fields workspace-visibility filtering reads.

    Filtering runs over every agent (visibility has to, to compute ``total``
    correctly); the full ``spec.to_dict()`` (tools, commands, remote
    descriptor, everything) does not, so it is deferred to the page slice.
    """
    return {
        "id": spec.id,
        "system": spec.system,
        "shared": spec.shared,
        "owner_workspace": spec.owner_workspace,
        "default_workspace_only": spec.default_workspace_only,
    }


@router.get("", response_model=Union[List[AgentListItem], AgentPage])
async def list_agents(workspace: Optional[str] = None, limit: Optional[int] = None,
                      offset: Optional[int] = None):
    """List all available agents from registry and factory definitions.

    Agents are assembled in memory from the registry and factory definitions
    (not a queryable store), so ``limit``/``offset`` slice the assembled list
    rather than being pushed into a query. With neither given, the response is
    the full list exactly as before; with either, it is one page:
    ``{items, total, limit, offset}``.

    Visibility filtering (workspace ownership, ``allowed_agents``,
    ``default_workspace_only``) runs over cheap fields for every candidate, since
    it decides ``total``; building the full agent dict and annotating it with
    live node status and the workspace's memory overrides only happens for the
    agents in the requested page.
    """
    # Get agents from existing registry
    registry_agents = registry.list_agents()
    reg_ids = {a.id for a in registry_agents}

    # Get agent definitions from factory
    factory = get_factory()
    factory_agents = factory.list_available_agents()

    _SYS_IDS = system_agent_ids()
    allowed = None
    if workspace and workspace != "default":
        from workspace import get_workspace_metadata
        allowed = get_workspace_metadata(workspace).get("allowed_agents")

    def _visible(light: Dict[str, Any]) -> bool:
        # Workspace-ownership visibility: a workspace-owned agent that is not
        # shared only appears in its owning workspace. Applies to every
        # workspace, including 'default'. System agents are always visible.
        if not _agent_visible_in_workspace(light, workspace):
            return False
        if workspace and workspace != "default":
            if allowed is not None and not (
                light["id"] in allowed
                or light["id"] in _SYS_IDS
                # An agent owned by this workspace is always available here
                # even if it was never explicitly added to allowed_agents.
                or light.get("owner_workspace") == workspace
            ):
                return False
            # Always hide default-workspace-only agents from non-default
            # workspaces (system agents are never default_workspace_only).
            if light.get("default_workspace_only", False):
                return False
        return True

    # Registry entries first, factory-only entries next: same order as before.
    # ``item`` is either the AgentSpec (full dict deferred) or the factory dict
    # (already the cheap shape, nothing further to defer).
    visible: List[Any] = [
        spec for spec in registry_agents if _visible(_light_registry_dict(spec))
    ]
    visible.extend(
        fa for fa in factory_agents
        if fa["id"] not in reg_ids and _visible(fa)
    )

    total = len(visible)
    if limit is None and offset is None:
        page_items = visible
    else:
        start = offset or 0
        page_items = visible[start: start + limit] if limit is not None else visible[start:]

    # Annotate each agent with whether it has a running node in the requested
    # workspace and flag system agents that cannot be removed. Memory
    # assignments are per-workspace, so patch them to the requesting
    # workspace's view. Only the page pays for any of this.
    from managers.node_manager import list_nodes
    _ws = (workspace or "default").strip() or "default"
    _mem_overrides = _workspace_memory_overrides(_ws)
    _default_chat_agent = _get_workspace_default_chat_agent(workspace)
    # One pass over every node instead of one list_nodes() call per agent —
    # list_nodes() re-syncs every node's live status (a process check each),
    # so calling it once per agent turned this into an O(agents * nodes)
    # liveness scan.
    _running_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for node in list_nodes():
        if node.get("status") != "running":
            continue
        _running_by_agent.setdefault(node.get("agent_id"), []).append(node)

    page: List[Dict[str, Any]] = []
    for item in page_items:
        agent = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        _apply_workspace_memory(agent, _ws, _mem_overrides)
        running_nodes = _running_by_agent.get(agent["id"], [])
        if workspace and workspace != "default":
            running_nodes = [n for n in running_nodes if n.get("workspace") == workspace]
        agent["has_running_node"] = len(running_nodes) > 0
        agent["system"] = agent["id"] in _SYS_IDS
        agent["is_default_chat_agent"] = agent["id"] == _default_chat_agent
        page.append(agent)

    if limit is None and offset is None:
        return page
    return {"items": page, "total": total, "limit": limit, "offset": offset}


@router.get("/tools")
async def list_tools():
    """List all available tools. Returns factory, swe, and all for backward compatibility."""
    # Get tools from centralized registry
    all_registry_tools = get_all_tools()
    
    # Convert registry tools to dict format expected by frontend
    def tool_spec_to_dict(spec):
        return {
            "name": spec.id,
            "label": spec.name,
            "category": spec.category,
            "description": spec.description,
            "args": {p["name"]: p["type"] for p in spec.parameters},
            # Security capabilities this tool grants — the agent editor uses
            # these to explain a blocked combination (see tools/capabilities.py).
            "capabilities": sorted(spec._grants),
        }
    
    registry_tools_dicts = [tool_spec_to_dict(t) for t in all_registry_tools]
    
    # For backward compatibility, return in the format the frontend expects
    return {
        "factory": registry_tools_dicts,  # All tools from registry
        "swe": registry_tools_dicts,      # Same tools (for compatibility)
        "all": registry_tools_dicts,      # Combined list
    }


@router.post("/capability-check")
async def capability_check(data: AgentToolsUpdate):
    """Classify a tool set and report any blocked capability combination.

    Lets the agent editor show the violation *while* the tools are being picked,
    with the offending capabilities and the tools that granted them named,
    instead of only failing on save with a generic error.
    """
    from tools.capabilities import explain
    from agents.capability_guard import guard_mode
    result = explain(list(data.tools))
    result["mode"] = guard_mode()
    return result


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent_details(agent_id: str, workspace: Optional[str] = None):
    from workspace import is_system_agent
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    data = spec.to_dict()
    data["system"] = is_system_agent(agent_id)
    data["is_default_chat_agent"] = agent_id == _get_workspace_default_chat_agent(workspace)
    ws = (workspace or "default").strip() or "default"
    _apply_workspace_memory(data, ws, _workspace_memory_overrides(ws))
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
    # The prompt may live in a shared definition folder (definition_id) rather
    # than under the agent's own id.
    def_id = spec.def_id()
    folder = prompt_assembly.agent_dir(def_id, definitions_dir=defs_dir)

    instructions = prompt_assembly.read_instructions(def_id, definitions_dir=defs_dir)
    capabilities = prompt_assembly.read_capabilities(def_id, definitions_dir=defs_dir)
    usage = prompt_assembly.read_usage(def_id, definitions_dir=defs_dir)

    # Assembled prompt only when instructions exist
    assembled = ""
    if instructions:
        assembled = prompt_assembly.assemble_prompt(def_id, definitions_dir=defs_dir)

    return {
        "agent_id": spec.id,
        "source": "markdown" if instructions else "missing",
        "definition_dir": str(folder),
        "system_prompt": assembled,
        "instructions": instructions,
        "capabilities": capabilities,
        "usage": usage,
    }


class AgentInstructionsUpdate(BaseModel):
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
    # Edits target the shared definition folder; when this agent shares its
    # definition with others, the change applies to all of them (expected).
    def_id = spec.def_id()

    # Capture the state this edit is about to replace, the same way
    # registry.add_agent does for a structured-field edit — this route never
    # calls add_agent (it only touches the markdown files), so it has to
    # snapshot on its own before writing. Passing the prospective content
    # (unset fields fall back to what is on disk now) lets the snapshot skip
    # a no-op save instead of padding history with an unchanged entry.
    agent_versions.snapshot_if_changed(
        agent_id,
        next_definition={
            "instructions": data.instructions if data.instructions is not None
                else prompt_assembly.read_instructions(def_id, definitions_dir=defs_dir),
            "capabilities": data.capabilities if data.capabilities is not None
                else prompt_assembly.read_capabilities(def_id, definitions_dir=defs_dir),
            "usage": data.usage if data.usage is not None
                else prompt_assembly.read_usage(def_id, definitions_dir=defs_dir),
        },
        actor="dashboard", note="definition edit",
    )

    if data.instructions is not None:
        if data.instructions.strip():
            prompt_assembly.write_instructions(def_id, data.instructions, definitions_dir=defs_dir)
        else:
            raise HTTPException(status_code=400, detail="instructions.md cannot be empty")
    if data.capabilities is not None:
        path = prompt_assembly.agent_dir(def_id, defs_dir) / prompt_assembly.CAPABILITIES_FILE
        if data.capabilities.strip():
            prompt_assembly.write_capabilities(def_id, data.capabilities, definitions_dir=defs_dir)
        elif path.exists():
            path.unlink()
    if data.usage is not None:
        path = prompt_assembly.agent_dir(def_id, defs_dir) / prompt_assembly.USAGE_FILE
        if data.usage.strip():
            prompt_assembly.write_usage(def_id, data.usage, definitions_dir=defs_dir)
        elif path.exists():
            path.unlink()

    return await get_agent_definition(agent_id)


@router.get("/{agent_id}/versions")
async def list_agent_versions(agent_id: str):
    """Version history: one entry per stored snapshot, oldest first, each
    with a summary of what changed relative to the entry before it."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "versions": agent_versions.list_versions(agent_id)}


@router.get("/{agent_id}/versions/{version}/diff")
async def diff_agent_version(agent_id: str, version: int, against: str = "current"):
    """Unified diff (per part: spec / instructions / capabilities / usage)
    between a stored version and either the current live state or another
    stored version (``against=current`` or ``against=<version number>``)."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    from_entry = agent_versions.get_version_row(agent_id, version)
    if from_entry is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    if against == "current":
        to_entry = agent_versions.current_snapshot(agent_id)
    else:
        try:
            to_version = int(against)
        except ValueError:
            raise HTTPException(status_code=400, detail="against must be 'current' or a version number")
        to_entry = agent_versions.get_version_row(agent_id, to_version)
        if to_entry is None:
            raise HTTPException(status_code=404, detail=f"Version {to_version} not found")

    return {
        "agent_id": agent_id,
        "from": version,
        "against": against,
        "diff": agent_versions.diff_entries(from_entry, to_entry),
    }


@router.post("/{agent_id}/versions/{version}/rollback")
async def rollback_agent_version(agent_id: str, version: int):
    """Restore a historical version as the agent's current state.

    Goes through ``registry.add_agent`` (via ``agent_versions.rollback_to``),
    so a tool combination the capability guard would now block is refused the
    same way a normal edit is.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        restored = agent_versions.rollback_to(agent_id, version, actor="dashboard")
    except CapabilityViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"agent_id": agent_id, "restored_to": version, "agent": restored}


@router.put("/{agent_id}/description")
async def update_agent_description(agent_id: str, data: AgentDescriptionUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = dataclasses.replace(spec, description=data.description.strip())
    registry.add_agent(new_spec)
    return {"description": new_spec.description}


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
async def get_agent_history(agent_id: str, workspace: Optional[str] = None):
    runs = run_manager.load_runs()
    agent_runs = [r for r in runs if r.get("agent_id") == agent_id]
    # Scope to the active workspace; the default workspace sees every workspace.
    if workspace and workspace != "default":
        agent_runs = [r for r in agent_runs if r.get("workspace") == workspace]
    # Sort by started_at desc
    agent_runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return agent_runs


@router.get("/{agent_id}/logs")
async def get_agent_logs(agent_id: str, node_id: Optional[str] = None, limit: int = 100,
                         workspace: Optional[str] = None):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    from managers import node_manager

    # Scope to the active workspace; the default workspace sees every workspace.
    ws_scoped = bool(workspace and workspace != "default")

    runs = run_manager.load_runs()
    agent_runs = [r for r in runs if r.get("agent_id") == agent_id]
    if ws_scoped:
        agent_runs = [r for r in agent_runs if r.get("workspace") == workspace]
    if node_id:
        agent_runs = [r for r in agent_runs if str(r.get("node_id") or "") == str(node_id)]
    agent_runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    if limit > 0:
        agent_runs = agent_runs[: max(1, min(int(limit), 500))]

    for r in agent_runs:
        lp = r.get("log_file")
        r["log_exists"] = bool(lp and Path(lp).exists())

    nodes = [n for n in node_manager.list_nodes() if n.get("agent_id") == agent_id]
    if ws_scoped:
        nodes = [n for n in nodes if n.get("workspace") == workspace]
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
    # Resolve the definition folder before removing the record so we can decide
    # whether deleting it would orphan sibling records sharing the same folder.
    spec = registry.get_agent(agent_id)
    def_id = spec.def_id() if spec else agent_id

    found = registry.remove_agent(agent_id)
    if not found:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Only delete the shared definition folder when no remaining record uses it.
    # This prevents one workspace's delete from breaking another's agent.
    factory = get_factory()
    still_in_use = any(s.def_id() == def_id for s in registry.list_agents())
    if not still_in_use:
        prompt_assembly.delete_definition(def_id, definitions_dir=factory.definitions_dir)

    # An imported agent also owns a clone under the state root; deleting the
    # record without it would leave the repository behind forever.
    if spec is not None and spec.is_remote():
        from agents.importer import cleanup as cleanup_import
        cleanup_import(agent_id)
    return {"message": f"Agent {agent_id} deleted"}


@router.post("/{agent_id}/memory")
async def update_agent_memory(agent_id: str, data: AgentMemoryUpdate):
    """Set the agent's memory assignment for a workspace.

    Memory is per-workspace: in the agent's home workspace the assignment is
    stored on the record; in any other workspace it is stored as that
    workspace's metadata override, so instances of a shared agent stay
    independent across workspaces.
    """
    from memory.binding import home_workspace

    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    memory_data = data.memory_data
    if data.memory_type == "shared":
        # Accept a single pool id or a list (primary first); normalize to a
        # deduped list, collapsed back to a plain string for a single pool so
        # legacy single-pool records keep their shape.
        raw = memory_data if isinstance(memory_data, (list, tuple)) else [memory_data]
        pools = []
        for p in raw:
            pid = str(p or "").strip()
            if pid and pid not in pools:
                pools.append(pid)
        if not pools:
            raise HTTPException(status_code=400, detail="At least one memory pool id is required for shared memory")
        memory_data = pools[0] if len(pools) == 1 else pools

    ws = (data.workspace or "default").strip() or "default"
    if ws == home_workspace(spec):
        new_spec = dataclasses.replace(spec, memory_type=data.memory_type, memory_data=memory_data)
        registry.add_agent(new_spec)
        return new_spec.to_dict()

    create_workspace_folder(ws)
    overrides = dict(_workspace_memory_overrides(ws))
    if data.memory_type and data.memory_type != "none":
        overrides[agent_id] = {"memory_type": data.memory_type, "memory_data": memory_data}
    else:
        overrides.pop(agent_id, None)
    update_workspace_metadata(ws, {"agent_memory_overrides": overrides})

    result = spec.to_dict()
    result["memory_type"] = data.memory_type
    result["memory_data"] = memory_data if data.memory_type != "none" else None
    return result


@router.delete("/{agent_id}/memory")
async def erase_agent_memory(agent_id: str, workspace: Optional[str] = None):
    """Remove the agent's memory assignment for a workspace (see update)."""
    from memory.binding import home_workspace

    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    ws = (workspace or "default").strip() or "default"
    if ws == home_workspace(spec):
        new_spec = dataclasses.replace(spec, memory_type="none", memory_data=None)
        registry.add_agent(new_spec)
        return new_spec.to_dict()

    overrides = dict(_workspace_memory_overrides(ws))
    overrides.pop(agent_id, None)
    update_workspace_metadata(ws, {"agent_memory_overrides": overrides})

    result = spec.to_dict()
    result["memory_type"] = "none"
    result["memory_data"] = None
    return result


@router.post("/{agent_id}/skills-config")
async def update_agent_skills_config(agent_id: str, data: AgentSkillsConfigUpdate):
    """Enable or disable procedural skills for this agent."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        definition_id=spec.definition_id,
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
        response_format=spec.response_format,
        clarify_gate=spec.clarify_gate,
        allow_self_delegation=spec.allow_self_delegation,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.get("/{agent_id}/episodic-config")
async def get_agent_episodic_config(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    # Tri-state setting plus the effective decision for this agent's provider.
    from agents.agent_factory import resolve_episodic_write, _factory
    try:
        provider, _m, _u, _k = _factory._resolve_model_config(spec.to_dict(), None)
    except Exception:
        provider = spec.provider
    return {
        "episodic_write_enabled": spec.episodic_write_enabled,  # null = auto
        "effective": resolve_episodic_write(spec.episodic_write_enabled, provider),
        "provider": provider,
    }


@router.post("/{agent_id}/episodic-config")
async def update_agent_episodic_config(agent_id: str, data: AgentEpisodicConfigUpdate):
    """Set the episodic write tool (record_episode) mode: auto (null) / on / off."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    new_spec = dataclasses.replace(spec, episodic_write_enabled=data.episodic_write_enabled)
    registry.add_agent(new_spec)
    from agents.agent_factory import resolve_episodic_write, _factory
    try:
        provider, _m, _u, _k = _factory._resolve_model_config(new_spec.to_dict(), None)
    except Exception:
        provider = new_spec.provider
    return {
        "episodic_write_enabled": new_spec.episodic_write_enabled,
        "effective": resolve_episodic_write(new_spec.episodic_write_enabled, provider),
        "provider": provider,
    }


@router.get("/{agent_id}/response-format")
async def get_agent_response_format(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"response_format": spec.response_format or "none"}


@router.post("/{agent_id}/response-format")
async def update_agent_response_format(agent_id: str, data: AgentResponseFormatUpdate):
    """Set the structured response format the agent may emit (none/buttons/telegram).

    When not "none", agent_factory injects a system-prompt snippet teaching the
    <<<ui>>> block convention so the agent can produce buttons / a Telegram
    inline keyboard.
    """
    from agents.agent_response import RESPONSE_FORMAT_CHOICES
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    fmt = data.response_format or "none"
    if fmt not in RESPONSE_FORMAT_CHOICES:
        raise HTTPException(status_code=400, detail=f"Invalid response_format '{fmt}'")
    new_spec = dataclasses.replace(spec, response_format=fmt)
    registry.add_agent(new_spec)
    return {"response_format": new_spec.response_format}


@router.get("/{agent_id}/clarify-gate")
async def get_agent_clarify_gate(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"clarify_gate": bool(spec.clarify_gate)}


@router.post("/{agent_id}/clarify-gate")
async def update_agent_clarify_gate(agent_id: str, data: AgentClarifyGateUpdate):
    """Toggle the chat clarification gate.

    When enabled, agent_factory injects a system-prompt snippet telling the agent
    to gather missing requirements (ask concise questions and stop) before
    executing, rather than acting on assumptions.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    new_spec = dataclasses.replace(spec, clarify_gate=bool(data.clarify_gate))
    registry.add_agent(new_spec)
    return {"clarify_gate": bool(new_spec.clarify_gate)}


@router.get("/{agent_id}/self-delegation")
async def get_agent_self_delegation(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"allow_self_delegation": bool(spec.allow_self_delegation)}


@router.post("/{agent_id}/self-delegation")
async def update_agent_self_delegation(agent_id: str, data: AgentSelfDelegationUpdate):
    """Toggle whether the agent may delegate to itself.

    When enabled, the agent may target its own id in run_agent_tool (and
    assign_agent_tool); the self-block in tools._delegation_blocked is lifted for
    this agent. Off by default because a self-run recurses the same agent.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    new_spec = dataclasses.replace(spec, allow_self_delegation=bool(data.allow_self_delegation))
    registry.add_agent(new_spec)
    return {"allow_self_delegation": bool(new_spec.allow_self_delegation)}


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
            "shared": bool(p.shared),
            "origin_skill_id": p.origin_skill_id,
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

    # dataclasses.replace rather than a field-by-field rebuild: the rebuild
    # silently dropped every field added to AgentSpec after it was written
    # (capability_override, delegates, ...), which for the capability guard
    # would mean editing a grandfathered agent's tools revoked its override.
    new_spec = dataclasses.replace(spec, tools=list(data.tools))
    try:
        registry.add_agent(new_spec)
    except CapabilityViolation as e:
        # 409, not 400: the request is well-formed, the resulting *state* is
        # refused. Carries the structured violation so the editor can name the
        # offending capabilities and the tools that granted them.
        raise HTTPException(
            status_code=409,
            detail={"error": "capability_violation", **e.violation.to_dict()},
        )
    return new_spec.to_dict()


@router.get("/{agent_id}/delegates")
async def get_agent_delegates(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"delegates": list(spec.delegates or [])}


@router.post("/{agent_id}/delegates")
async def update_agent_delegates(agent_id: str, data: AgentDelegatesUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    # Normalize: strip, dedupe, drop blanks. Empty list = no restriction.
    cleaned: list[str] = []
    for d in data.delegates:
        did = str(d).strip()
        if did and did not in cleaned:
            cleaned.append(did)
    new_spec = dataclasses.replace(spec, delegates=cleaned)
    registry.add_agent(new_spec)
    return new_spec.to_dict()


@router.get("/{agent_id}/reasoning")
async def get_agent_reasoning(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    from reasoning import resolve_reasoning
    return resolve_reasoning(spec.reasoning, spec.tools)


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
    if data.thinking_level is not None:
        current["thinking_level"] = data.thinking_level
    if data.plan_enabled is not None:
        current["plan_enabled"] = data.plan_enabled
    if data.plan_format is not None:
        current["plan_format"] = data.plan_format

    new_spec = registry.AgentSpec(
        id=spec.id,
        definition_id=spec.definition_id,
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
        response_format=spec.response_format,
        clarify_gate=spec.clarify_gate,
        allow_self_delegation=spec.allow_self_delegation,
    )
    registry.add_agent(new_spec)
    from reasoning import resolve_reasoning
    return resolve_reasoning(current, spec.tools)


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
        definition_id=spec.definition_id,
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
        response_format=spec.response_format,
        clarify_gate=spec.clarify_gate,
        allow_self_delegation=spec.allow_self_delegation,
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
    if allowed is not None and agent_id not in allowed and agent_id not in system_agent_ids():
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

    factory = get_factory()

    # When definition_id is supplied, reuse an existing shared definition folder
    # instead of authoring a new one (system_prompt is ignored).
    definition_id = (data.definition_id or "").strip() or None
    if definition_id:
        if not prompt_assembly.has_definition(definition_id, definitions_dir=factory.definitions_dir):
            raise HTTPException(
                status_code=400,
                detail=f"definition '{definition_id}' does not exist",
            )
    else:
        if not data.system_prompt or not data.system_prompt.strip():
            raise HTTPException(status_code=400, detail="system_prompt is required")
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
        definition_id=definition_id,
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
    except CapabilityViolation as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "capability_violation", **e.violation.to_dict()},
        )
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


@router.post("/{agent_id}/clone-to-workspace")
async def clone_agent_to_workspace(agent_id: str, data: AgentCloneToWorkspace):
    """Create a workspace-scoped copy of an agent that shares its definition.

    The new record copies all of the source agent's settings (model, memory,
    tools, etc.) but is bound to ``data.workspace`` via ``owner_workspace`` and
    points at the same definition folder via ``definition_id`` — so editing the
    prompt is shared, while model/memory settings can diverge per workspace.
    """
    source = registry.get_agent(agent_id)
    if not source:
        raise HTTPException(status_code=404, detail="Agent not found")

    workspace = (data.workspace or "").strip()
    if not workspace:
        raise HTTPException(status_code=400, detail="workspace is required")

    def_id = source.def_id()

    new_id = (data.new_id or "").strip()
    if not new_id:
        new_id = f"{def_id}@{workspace}"
    # Keep ids filesystem/registry friendly, matching the create wizard's slug style.
    new_id = re.sub(r"\s+", "_", new_id).lower()

    if registry.get_agent(new_id) is not None:
        raise HTTPException(status_code=400, detail=f"Agent '{new_id}' already exists")

    owner_workspace = workspace if workspace != "default" else None

    # Copy every setting from the source, overriding only identity/ownership.
    # definition_id is set explicitly so the new record reuses the shared folder
    # (no write_instructions). shared=False keeps the copy scoped to its workspace.
    new_spec = dataclasses.replace(
        source,
        id=new_id,
        definition_id=def_id,
        owner_workspace=owner_workspace,
        shared=False,
        is_default_chat_agent=False,
    )
    try:
        registry.add_agent(new_spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Make the copy visible in its workspace immediately.
    if owner_workspace:
        try:
            create_workspace_folder(owner_workspace)
            metadata = get_workspace_metadata(owner_workspace)
            allowed = list(metadata.get("allowed_agents") or [])
            if new_id not in allowed:
                allowed.append(new_id)
                update_workspace_metadata(owner_workspace, {"allowed_agents": allowed})
        except Exception:
            # Best-effort; the record itself is created and owner_workspace is set.
            pass

    return new_spec.to_dict()


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
        definition_id=spec.definition_id,
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
        response_format=spec.response_format,
        clarify_gate=spec.clarify_gate,
        allow_self_delegation=spec.allow_self_delegation,
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()


# ── The definition chat ──────────────────────────────────────────────────────
#
# Same shape as the loop, team, scenario and world build chats, with one
# difference in kind: the entity under edit is an agent's own definition. That
# makes the warning below load-bearing — an agent editing a *system* agent is
# detaching it from the shipped seed, and the user should hear that before it
# happens rather than discover it at the next upgrade.

DEFINITION_AGENT_ID = "agent_creator"
DEFINITION_CHAT_KIND = "agentdef"


def _definition_state(agent_id: str) -> Dict[str, Any]:
    """What the agent is editing: the record's editable fields plus the three
    markdown files that become the system prompt."""
    spec = registry.get_agent(agent_id)
    if spec is None:
        return {}
    return {
        "agent_id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "domain": spec.domain,
        "system": spec.system,
        "user_modified": spec.user_modified,
        "tools": list(spec.tools or []),
        "delegates": list(spec.delegates or []),
        "provider": spec.provider,
        "model": spec.model,
        "temperature": spec.temperature,
        "reasoning": dict(spec.reasoning or {}),
        "skills_enabled": spec.skills_enabled,
        "instructions": prompt_assembly.read_instructions(spec.def_id()),
        "capabilities": prompt_assembly.read_capabilities(spec.def_id()),
        "usage": prompt_assembly.read_usage(spec.def_id()),
    }


def _tool_catalog() -> List[Dict[str, str]]:
    """Every tool that could be granted, with what it costs in capability terms.

    The capability flags are in here because the guard will refuse a bad
    combination at save time, and an agent that knows why beforehand can propose
    a workable set instead of discovering the refusal.
    """
    return [
        {"id": t.id, "name": t.name, "category": t.category,
         "description": t.description,
         "grants": ", ".join(sorted(t._grants)) or "nothing"}
        for t in get_all_tools()
    ]


def _definition_chat_prompt(agent_id: str, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the agent under edit, the tools it could hold, the talk."""
    from chat.entity_chat import transcript_block

    state = _definition_state(agent_id)
    parts = [
        "You are editing ONE agent's definition in this platform. The user is "
        "looking at its page: every change you make with modify_agent_tool "
        "appears in the panels beside this chat.",
        "",
        f"Agent under edit: {state.get('name')} (agent_id: {agent_id})",
        "",
        "=== Current definition ===",
        json.dumps(state, ensure_ascii=False, indent=2),
        "",
        "=== Tools that could be granted ===",
        json.dumps(_tool_catalog(), ensure_ascii=False, indent=2),
        "",
        "Rules for this conversation:",
        f"- Apply every change to agent_id '{agent_id}' with modify_agent_tool. "
        "Never create a second agent unless the user explicitly asks for one.",
        "- `system_prompt` APPENDS to instructions.md by default. For a rewrite "
        "pass replace_system_prompt=true. Know which one you are doing and say so.",
        "- instructions.md, capabilities.md and usage.md are all concatenated "
        "into the system prompt. So capabilities.md must describe tools the "
        "agent actually holds: a tool named there that it does not have is a "
        "promise the runtime cannot keep, and the agent will try the call and "
        "fail. If you change the tool list, update capabilities.md in the same "
        "turn.",
        "- Granting tools can be refused. An agent that can read private data, "
        "ingest untrusted text AND send data outside is blocked outright. Check "
        "the grants column above before proposing a set, and if the job really "
        "needs all three, propose splitting it across two agents instead.",
        "- When the user only asks a question, answer it without changing anything.",
        "- Finish with one short paragraph: what you changed and what the agent "
        "will now do differently.",
    ]
    if state.get("system") and not state.get("user_modified"):
        parts.insert(4, (
            "!! This is a SYSTEM agent. It ships with the product and is kept in "
            "sync with what the product ships. The first edit marks it as the "
            "operator's and it stops receiving those updates, permanently. Say "
            "this plainly and get a clear yes before your first modify call in "
            "this conversation. A question about the agent is not a yes."
        ))
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _load_definition_chat(request):
    """Path param only: the agent under edit, 404 when it does not exist."""
    from types import SimpleNamespace

    agent_id = request.path_params["agent_id"]
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return SimpleNamespace(entity_id=agent_id, workspace=None, agent_spec=spec)


def _load_definition_send(request, body):
    from common.bootstrap import ensure_system_agent

    ctx = _load_definition_chat(request)
    if not ensure_system_agent(DEFINITION_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{DEFINITION_AGENT_ID}' agent is not registered")
    ctx.workspace = request.query_params.get("workspace")
    ctx.before = _definition_state(ctx.entity_id)
    return ctx


def _definition_summarize(ctx):
    def _summarize() -> str:
        after = _definition_state(ctx.entity_id)
        if not after:
            return "The agent is gone."
        before = ctx.before
        if after == before:
            return ""
        bits = []
        if after["instructions"] != before["instructions"]:
            bits.append("rewrote its instructions")
        if after["capabilities"] != before["capabilities"]:
            bits.append("updated what it says it can do")
        if after["usage"] != before["usage"]:
            bits.append("updated when to use it")
        if after["tools"] != before["tools"]:
            was, now = len(before["tools"]), len(after["tools"])
            bits.append(f"changed its tools ({was} → {now})")
        if after["model"] != before["model"] or after["provider"] != before["provider"]:
            bits.append("changed its model")
        if after["description"] != before["description"]:
            bits.append("rewrote its description")
        return ("Done — " + ", ".join(bits) + ".") if bits else "Done — the agent was updated."
    return _summarize


def _definition_context_setup(ctx):
    if ctx.workspace:
        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ctx.workspace)


async def _definition_post_turn(queue, ctx):
    after = registry.get_agent(ctx.entity_id)
    if after:
        await queue.put({"type": "agentdef", "agent": _definition_state(ctx.entity_id)})


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=DEFINITION_CHAT_KIND,
    path="/{agent_id}/definition/chat",
    load=_load_definition_chat,
    load_for_send=_load_definition_send,
    prompt=lambda ctx, history, msg: _definition_chat_prompt(ctx.entity_id, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=DEFINITION_CHAT_KIND, agent_id=DEFINITION_AGENT_ID,
        title=f"{ctx.agent_spec.name} · definition", workspace=ctx.workspace,
    ),
    summarize=_definition_summarize,
    context_setup=_definition_context_setup,
    post_turn=_definition_post_turn,
)))
