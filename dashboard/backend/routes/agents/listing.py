"""Agent list, details, tool catalog and workspace capacities."""

from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional, Union

from agents import registry
from tools.registry import get_all_tools
from agents.capability_guard import (
    guard_mode,
)
from models import AgentToolsUpdate, AgentListItem, AgentDetail, AgentPage
from workspace import system_agent_ids



from ._common import (package, get_factory, _agent_visible_in_workspace, _apply_workspace_memory, _get_workspace_default_chat_agent, _light_registry_dict, _workspace_memory_overrides)

router = APIRouter(prefix="/api/agents", tags=["agents"])

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

    # Annotate each agent with whether it has a running resident instance in
    # the requested workspace and flag system agents that cannot be removed.
    # Memory assignments are per-workspace, so patch them to the requesting
    # workspace's view. Only the page pays for any of this.
    from instances.carrier import list_resident
    _ws = (workspace or "default").strip() or "default"
    _mem_overrides = _workspace_memory_overrides(_ws)
    _default_chat_agent = _get_workspace_default_chat_agent(workspace)
    # One pass over every resident instance instead of one list_resident()
    # call per agent — list_resident() re-syncs every instance's live status
    # (a process check each), so calling it once per agent turned this into
    # an O(agents * instances) liveness scan.
    _running_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for instance in list_resident(live=True):
        _running_by_agent.setdefault(str(instance.get("agent_id")), []).append(instance)
    # Inheritance (agents/inheritance.py): how many agents extend each one.
    _children_count: Dict[str, int] = {}
    for _raw in registry.list_agents_raw():
        if _raw.extends:
            _parent = registry.resolve_agent_id(_raw.extends)
            _children_count[_parent] = _children_count.get(_parent, 0) + 1

    page: List[Dict[str, Any]] = []
    for item in page_items:
        agent = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        _apply_workspace_memory(agent, _ws, _mem_overrides)
        running_instances = _running_by_agent.get(agent["id"], [])
        if workspace and workspace != "default":
            running_instances = [i for i in running_instances if i.get("workspace") == workspace]
        agent["has_running_node"] = len(running_instances) > 0
        agent["system"] = agent["id"] in _SYS_IDS
        agent["is_default_chat_agent"] = agent["id"] == _default_chat_agent
        agent.setdefault("extends", None)
        agent.setdefault("extends_version", None)
        agent["children_count"] = _children_count.get(agent["id"], 0)
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


def _workspace_capacity_overrides() -> Dict[str, Dict[str, Any]]:
    """``{agent_id: {workspace: capacity}}`` over every workspace but default,
    read in one pass over the workspace metadata."""
    from workspace import list_workspace_folders
    result: Dict[str, Dict[str, Any]] = {}
    for ws_path in list_workspace_folders():
        ws_name = ws_path.name
        if ws_name == "default":
            continue
        overrides = package().get_workspace_metadata(ws_name).get("agent_capacity_overrides") or {}
        for agent_id, capacity in overrides.items():
            result.setdefault(agent_id, {})[ws_name] = capacity
    return result


@router.get("/workspace-capacities")
async def get_all_workspace_capacities():
    """Every agent's workspace capacity overrides at once, for the agents list:
    one request for the page instead of one per agent card. Declared before
    the ``/{agent_id}`` routes, which would otherwise take the path."""
    return _workspace_capacity_overrides()


@router.post("/capability-check")
async def capability_check(data: AgentToolsUpdate):
    """Classify a tool set and report any blocked capability combination.

    Lets the agent editor show the violation *while* the tools are being picked,
    with the offending capabilities and the tools that granted them named,
    instead of only failing on save with a generic error.
    """
    from tools.capabilities import explain
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
    # Inheritance (agents/inheritance.py): the effective spec above, plus the
    # parent link and the agents that extend this one.
    data.setdefault("extends", None)
    data.setdefault("extends_version", None)
    data["children"] = registry.children_of(spec.id)
    ws = (workspace or "default").strip() or "default"
    _apply_workspace_memory(data, ws, _workspace_memory_overrides(ws))
    return data


