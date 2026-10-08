"""Agent history, logs, delete and the per-agent memory, skills and response settings."""
import dataclasses

from fastapi import APIRouter, HTTPException
from typing import Optional
from pathlib import Path

from agents import registry
from managers import run_manager
from agents import prompt_assembly
from models import AgentMemoryUpdate, AgentSkillsConfigUpdate, AgentEpisodicConfigUpdate, AgentPersonalMemoryUpdate, AgentResponseFormatUpdate, AgentClarifyGateUpdate, AgentSelfDelegationUpdate, AgentSkillCreate



from ._common import (package, get_factory, _workspace_memory_overrides)
from .listing import _workspace_capacity_overrides

router = APIRouter(prefix="/api/agents", tags=["agents"])

@router.get("/{agent_id}/workspace-capacities")
async def get_agent_workspace_capacities(agent_id: str):
    """Return workspace-specific capacity overrides for this agent (excludes 'default')."""
    return _workspace_capacity_overrides().get(agent_id, {})


@router.get("/{agent_id}/history")
async def get_agent_history(agent_id: str, workspace: Optional[str] = None, limit: int = 200):
    """This agent's newest runs, paged in SQL like /api/messages."""
    # Scope to the active workspace; the default workspace sees every workspace.
    ws = workspace if workspace and workspace != "default" else None
    page = run_manager.query_runs(agent_id=agent_id, workspace=ws, limit=max(1, min(int(limit), 500)))
    return page["items"]


@router.get("/{agent_id}/logs")
async def get_agent_logs(agent_id: str, node_id: Optional[str] = None, limit: int = 100,
                         workspace: Optional[str] = None):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    from instances.carrier import list_resident

    # Scope to the active workspace; the default workspace sees every workspace.
    ws_scoped = bool(workspace and workspace != "default")

    # Paged in SQL: loading every run ever recorded to keep this agent's
    # newest few is a full scan on each open of the agent page.
    agent_runs = run_manager.query_runs(
        agent_id=agent_id,
        workspace=workspace if ws_scoped else None,
        node_id=node_id or None,
        limit=max(1, min(int(limit), 500)) if limit > 0 else 500,
    )["items"]

    for r in agent_runs:
        lp = r.get("log_file")
        r["log_exists"] = bool(lp and Path(lp).exists())

    # ``node_id`` keeps its name on the query string for callers still passing
    # the id of a migrated node; store.get_by_node resolves it to the instance
    # it became, so it is matched by instance_id here.
    instances = [i for i in list_resident() if i.get("agent_id") == agent_id]
    if ws_scoped:
        instances = [i for i in instances if i.get("workspace") == workspace]
    if node_id:
        instances = [i for i in instances if str(i.get("instance_id") or "") == str(node_id)]
    instances.sort(key=lambda i: i.get("started_at") or "", reverse=True)
    for i in instances:
        lp = i.get("carrier_log_file")
        i["log_exists"] = bool(lp and Path(lp).exists())

    return {
        "agent_id": agent_id,
        "runs": agent_runs,
        "nodes": instances,
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

    # A parent other agents extend cannot go (agents/inheritance.py).
    from agents.inheritance import AgentHasChildren
    try:
        found = registry.remove_agent(agent_id)
    except AgentHasChildren as e:
        raise HTTPException(status_code=409, detail={
            "error": "agent_has_children", "message": str(e), "children": e.children,
        })
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
        # Accept a single pool id, a list (primary first), or an entry shaped
        # {"id": pool_id, "read_only": true} marking that one binding read
        # only (agents.registry.memory_pool_read_only_ids); normalize to a
        # deduped list, collapsed back to a plain id for a single, writable
        # pool so legacy single-pool records keep their shape.
        raw = memory_data if isinstance(memory_data, (list, tuple)) else [memory_data]
        pools = []
        seen_ids = set()
        for p in raw:
            if isinstance(p, dict):
                pid = str(p.get("id") or "").strip()
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                pools.append({"id": pid, "read_only": True} if p.get("read_only") else pid)
            else:
                pid = str(p or "").strip()
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                pools.append(pid)
        if not pools:
            raise HTTPException(status_code=400, detail="At least one memory pool id is required for shared memory")
        memory_data = pools[0] if len(pools) == 1 and not isinstance(pools[0], dict) else pools

    ws = (data.workspace or "default").strip() or "default"
    if ws == home_workspace(spec):
        new_spec = dataclasses.replace(spec, memory_type=data.memory_type, memory_data=memory_data)
        registry.add_agent(new_spec)
        return new_spec.to_dict()

    package().create_workspace_folder(ws)
    overrides = dict(_workspace_memory_overrides(ws))
    if data.memory_type and data.memory_type != "none":
        overrides[agent_id] = {"memory_type": data.memory_type, "memory_data": memory_data}
    else:
        overrides.pop(agent_id, None)
    package().update_workspace_metadata(ws, {"agent_memory_overrides": overrides})

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
    package().update_workspace_metadata(ws, {"agent_memory_overrides": overrides})

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

    # dataclasses.replace, not a field-by-field rebuild: the rebuild dropped
    # every field added to AgentSpec after it was written (delegates, handoffs,
    # the loop policies, secrets, ...), the bug routes/agents.py's tools route
    # already fixed the same way.
    new_spec = dataclasses.replace(spec, skills_enabled=data.skills_enabled)
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


def _personal_memory_config(spec, workspace: Optional[str]) -> dict:
    from memory import personal
    from memory.binding import effective_memory_pools
    settings = personal.agent_settings(workspace)
    own = settings["agents"].get(spec.id, False)
    return {
        "workspace": settings["workspace"],
        # This agent's switch, and whether the workspace has personal memory
        # at all: off there, the switch is shown off and cannot be changed.
        "enabled": own,
        "workspace_enabled": settings["enabled"],
        "is_main_agent": settings["main_agent"] == spec.id,
        "effective": settings["enabled"] and own,
        # Whether a pool of the agent's own is attached too: the agent then
        # has both, its own as the primary one (memory/personal.py).
        "has_own_pool": bool(effective_memory_pools(spec, workspace)),
    }


@router.get("/{agent_id}/personal-memory")
async def get_agent_personal_memory(agent_id: str, workspace: Optional[str] = None):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _personal_memory_config(spec, workspace)


@router.post("/{agent_id}/personal-memory")
async def update_agent_personal_memory(agent_id: str, data: AgentPersonalMemoryUpdate,
                                       workspace: Optional[str] = None):
    """Turn personal memory (memory/personal.py) on or off for this agent in
    one workspace. Refused while the workspace has personal memory off."""
    from memory import personal
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        personal.set_agent(agent_id, workspace, data.enabled)
    except personal.PersonalMemoryDisabled as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _personal_memory_config(spec, workspace)


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
    # The skills page's shape (version, pin, origin state, repo location),
    # so both pages read one record.
    from routes.skills import _to_dict as skill_to_dict
    store = ProcedureStore(workspace)
    loaded = store.load()
    procedures = [p for p in loaded if p.agent_id == agent_id]
    out = [skill_to_dict(p) for p in procedures]
    # Skills of the agents this one extends (agents/inheritance.py) reach it
    # at run time; listed after its own, read only, with where they come from.
    from agents.inheritance import ancestor_ids
    own_names = {p.name.strip().lower() for p in procedures}
    for ancestor in ancestor_ids(agent_id):
        for p in loaded:
            if p.agent_id == ancestor and p.name.strip().lower() not in own_names:
                own_names.add(p.name.strip().lower())
                out.append({**skill_to_dict(p), "inherited_from": ancestor, "read_only": True})
    return out


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

