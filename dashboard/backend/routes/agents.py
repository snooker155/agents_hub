"""
Agent-related API routes.

Includes the agent's own definition chat (``/{agent_id}/definition/chat``),
where the Agent Creator edits an agent's instructions, capabilities and usage in
place while the user watches the files change beside the conversation.
"""
import json
import re
import dataclasses

from fastapi import APIRouter, HTTPException, Request
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
from pydantic import BaseModel, Field

from agents import registry
from managers import run_manager
from agents.agent_factory import get_factory
from agents import prompt_assembly
from agents import versions as agent_versions
from tools.registry import get_all_tools
from agents.capability_guard import (
    CapabilityViolation, capability_warning, guard_mode, is_system_workspace_agent,
    override_honoured_at_build, override_requires_container,
)
from models import AgentCreateCustom, AgentCloneToWorkspace, AgentMemoryUpdate, AgentToolsUpdate, AgentDelegatesUpdate, AgentHandoffsUpdate, AgentModelUpdate, AgentReasoningUpdate, AgentSkillsConfigUpdate, AgentEpisodicConfigUpdate, AgentPersonalMemoryUpdate, AgentResponseFormatUpdate, AgentClarifyGateUpdate, AgentSelfDelegationUpdate, AgentCapabilityOverrideUpdate, AgentSkillCreate, AgentSharingUpdate, AgentDescriptionUpdate, AgentListItem, AgentDetail, AgentPage
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
    from memory import personal
    ws_name = _workspace_for_chat_default(workspace)
    create_workspace_folder(ws_name)
    # A new main agent gets personal memory on; the old one keeps what it had.
    personal.main_agent_changed(ws_name, personal.main_agent(ws_name), agent_id or None)
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
        _running_by_agent.setdefault(instance.get("agent_id"), []).append(instance)
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
        overrides = get_workspace_metadata(ws_name).get("agent_capacity_overrides") or {}
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
    # Inheritance (agents/inheritance.py): the effective spec above, plus the
    # parent link and the agents that extend this one.
    data.setdefault("extends", None)
    data.setdefault("extends_version", None)
    data["children"] = registry.children_of(spec.id)
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

    # Assembled prompt only when instructions exist. A child (extends) runs
    # its own text merged into its parent chain's (agents/inheritance.py):
    # system_prompt is that effective prompt, inherited_instructions the
    # parent's effective instructions its own text is merged into.
    assembled = ""
    inherited = ""
    if spec.extends:
        from agents import inheritance
        assembled = inheritance.effective_prompt(spec, definitions_dir=defs_dir)
        inherited = inheritance.inherited_instructions(spec, definitions_dir=defs_dir)
    elif instructions:
        assembled = prompt_assembly.assemble_prompt(def_id, definitions_dir=defs_dir)

    return {
        "agent_id": spec.id,
        "source": "markdown" if (instructions or spec.extends) else "missing",
        "definition_dir": str(folder),
        "system_prompt": assembled,
        "instructions": instructions,
        "capabilities": capabilities,
        "usage": usage,
        "extends": spec.extends,
        "inherited_instructions": inherited,
    }


class AgentInstructionsUpdate(BaseModel):
    instructions: Optional[str] = None
    capabilities: Optional[str] = None
    usage: Optional[str] = None
    # Optimistic concurrency (agents/revision.py): the stored version number
    # or the definition hash this edit was made against. A definition that
    # moved since is a 409 (checked by AgentRevisionMiddleware before this
    # route runs; the If-Match header does the same on every agent write).
    expected_version: Optional[Union[int, str]] = None


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

    prev_instructions = prompt_assembly.read_instructions(def_id, definitions_dir=defs_dir)
    prev_capabilities = prompt_assembly.read_capabilities(def_id, definitions_dir=defs_dir)
    prev_usage = prompt_assembly.read_usage(def_id, definitions_dir=defs_dir)
    next_instructions = data.instructions if data.instructions is not None else prev_instructions
    next_capabilities = data.capabilities if data.capabilities is not None else prev_capabilities
    next_usage = data.usage if data.usage is not None else prev_usage
    content_changed = (next_instructions != prev_instructions
                       or next_capabilities != prev_capabilities
                       or next_usage != prev_usage)

    # Capture the state this edit is about to replace, the same way
    # registry.add_agent does for a structured-field edit — this route never
    # calls add_agent (it only touches the markdown files), so it has to
    # snapshot on its own before writing. Passing the prospective content
    # (unset fields fall back to what is on disk now) lets the snapshot skip
    # a no-op save instead of padding history with an unchanged entry.
    agent_versions.snapshot_if_changed(
        agent_id,
        next_definition={
            "instructions": next_instructions,
            "capabilities": next_capabilities,
            "usage": next_usage,
        },
        actor="dashboard", note="definition edit",
    )

    if data.instructions is not None:
        # A child's own instructions may be empty: it then runs its parent's.
        if data.instructions.strip() or spec.extends:
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

    # A published, approved agent's definition changing is exactly the case
    # the review gate exists for: whatever passed review before may not
    # describe what the agent does now. Re-review is automatic, not an
    # accusation — the note says why, not that anything is wrong.
    if (content_changed and spec.shared and spec.review_status == "approved"
            and registry.registry_review_required()):
        try:
            registry.set_review_status(agent_id, "in_review", note="definition changed")
        except ValueError:
            pass

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


# ── A/B experiments between stored versions (evals/experiments.py) ──────────

class ExperimentArm(BaseModel):
    # A version number from the history, or "current" for the live
    # definition (snapshotted into history when it is not there yet).
    version: Union[int, str]
    share: float


class ExperimentUpdate(BaseModel):
    enabled: bool = True
    arms: List[ExperimentArm]
    note: Optional[str] = None


@router.get("/{agent_id}/experiment")
async def get_agent_experiment(agent_id: str):
    """The agent's open experiment, or the last ended one (``active`` says
    which), or ``experiment: null`` when it never had one."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    exp = experiments.get_latest(agent_id)
    return {"agent_id": agent_id, "experiment": exp,
            "active": bool(exp and not exp.get("ended_at"))}


@router.put("/{agent_id}/experiment")
async def put_agent_experiment(agent_id: str, data: ExperimentUpdate):
    """Start, pause, resume or change the agent's experiment. Arms reference
    stored versions and their shares must sum to 1; changing the arms ends
    the open experiment and starts a new one."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    arms = []
    for arm in data.arms:
        version = arm.version
        if isinstance(version, str):
            if version.strip().lower() == "current":
                version = agent_versions.ensure_current_version(agent_id, actor="dashboard")
                if version is None:
                    raise HTTPException(status_code=400, detail="Could not snapshot the current definition")
            else:
                try:
                    version = int(version)
                except ValueError:
                    raise HTTPException(status_code=400, detail="version must be a number or 'current'")
        arms.append({"version": version, "share": arm.share})
    try:
        exp = experiments.put_experiment(agent_id, enabled=data.enabled, arms=arms,
                                         note=data.note or "", actor="dashboard")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"agent_id": agent_id, "experiment": exp, "active": True}


@router.delete("/{agent_id}/experiment")
async def end_agent_experiment(agent_id: str):
    """End the open experiment. Its assignments and report stay readable."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    exp = experiments.end_experiment(agent_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="No experiment is running")
    return {"agent_id": agent_id, "experiment": exp, "active": False}


@router.get("/{agent_id}/experiment/report")
async def agent_experiment_report(agent_id: str, experiment_id: Optional[str] = None):
    """Per arm: runs, completed and failed, mean cost, tokens and duration,
    and the online eval score of the arm's runs. Reads the open experiment,
    else the last ended one, else the one named by ``experiment_id``."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    exp = (experiments.get_experiment(experiment_id) if experiment_id
           else experiments.get_latest(agent_id))
    if exp is None or exp.get("agent_id") != agent_id:
        raise HTTPException(status_code=404, detail="No experiment for this agent")
    return {"agent_id": agent_id, **experiments.report(exp)}


# ── Online evals (evals/online.py) ──────────────────────────────────────────

@router.get("/{agent_id}/online-evals")
async def list_agent_online_evals(agent_id: str, limit: int = 50):
    """The agent's most recent online eval results, newest first."""
    from evals import online

    return {"agent_id": agent_id, "results": online.recent_results(agent_id, limit)}


@router.get("/{agent_id}/online-evals/summary")
async def agent_online_evals_summary(agent_id: str):
    """Count, mean score and pass rate, overall, by definition version and
    by rule."""
    from evals import online

    return online.summary(agent_id)


@router.put("/{agent_id}/description")
async def update_agent_description(agent_id: str, data: AgentDescriptionUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = dataclasses.replace(spec, description=data.description.strip())
    registry.add_agent(new_spec)
    return {"description": new_spec.description}


class AgentIdentityUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    capacity: Optional[int] = Field(None, ge=1)


@router.put("/{agent_id}/identity")
async def update_agent_identity(agent_id: str, data: AgentIdentityUpdate):
    """Rename an agent, or change its domain or capacity.

    The id stays: it is what runs, tasks, locks and links address, so this
    changes only the label and the two plain settings set at creation.
    ``registry.add_agent`` snapshots the previous state into the version
    history, as for every other structured edit.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    changes: Dict[str, Any] = {}
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(status_code=400, detail="name cannot be empty")
        changes["name"] = data.name.strip()
    if data.domain is not None:
        changes["domain"] = data.domain.strip() or "general"
    if data.capacity is not None:
        changes["capacity"] = data.capacity
    if changes:
        registry.add_agent(dataclasses.replace(spec, **changes))
        spec = registry.get_agent(agent_id) or spec
    return {"name": spec.name, "domain": spec.domain, "capacity": spec.capacity}


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


def _capability_conflict(e: CapabilityViolation) -> HTTPException:
    """409, not 400: the request is well-formed, the resulting *state* is
    refused. Carries the structured violation so the editor can name the
    offending capabilities and the tools (or delegation hops) that granted
    them, plus the two switches that would let the save through.

    A parent's save refused for a child that inherits from it carries
    ``error = "inherited_capability_violation"`` and the child's ``agent_id``."""
    from agents.capability_guard import InheritedCapabilityViolation
    if isinstance(e, InheritedCapabilityViolation):
        return HTTPException(status_code=409, detail={"guard_mode": guard_mode(), **e.detail()})
    return HTTPException(
        status_code=409,
        detail={
            "error": "capability_violation",
            "guard_mode": guard_mode(),
            **e.violation.to_dict(),
        },
    )


def _capability_warning_dict(spec) -> Optional[Dict[str, Any]]:
    """The combination a just-saved record still forms, for the response.

    A save that went through on the per-agent override, in warn mode or by
    grandfathering has not made the exposure go away; the editor shows it as
    an amber banner. Delegation paths cannot be evaluated client-side (the
    delegates' tool lists are not in the page), so this is the only place the
    delegation card learns about them."""
    from tools.capabilities import secret_grant_ids
    v = capability_warning(
        spec.id,
        list(spec.tools or []) + secret_grant_ids(spec.secrets),
        delegates=list(spec.delegates or []),
        workspace=getattr(spec, "owner_workspace", None),
    )
    return v.to_dict() if v is not None else None


def _capability_override_state(spec) -> Dict[str, Any]:
    return {
        "capability_override": bool(spec.capability_override),
        "guard_mode": guard_mode(),
        "override_requires_container": override_requires_container(),
        # Whether the override, once on, actually lifts the block when the
        # agent is built: not in block mode with the container requirement on
        # and a local execution mode. Shown next to the switch.
        "honoured_at_build": override_honoured_at_build(spec.id),
        "capability_warning": _capability_warning_dict(spec),
    }


@router.get("/{agent_id}/auto-tools")
async def get_agent_auto_tools(agent_id: str, workspace: Optional[str] = None):
    """The tools the factory adds to this agent at build time on top of its
    record (agents/auto_tools.py), each with the setting that brings it, so
    the Tools tab can list everything a run will hold."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    from agents.auto_tools import auto_injected_tools
    ws = workspace or getattr(spec, "owner_workspace", None) or None
    return {"agent_id": agent_id, "workspace": ws, "tools": auto_injected_tools(spec, ws)}


@router.get("/{agent_id}/capability-override")
async def get_agent_capability_override(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _capability_override_state(spec)


@router.post("/{agent_id}/capability-override")
async def update_agent_capability_override(agent_id: str, data: AgentCapabilityOverrideUpdate):
    """Accept, for this one agent, a tool combination the capability guard
    would otherwise refuse (tools/capabilities.py, the lethal trifecta).

    With the override on, a blocked combination is saved and reported as a
    warning instead of refused, whether it comes from the agent's own tools
    or from an agent it may delegate to. Turning the override back off never
    fails: the record keeps the combination it already holds (grandfathered)
    and the next tool or delegate that would widen it is refused again.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if data.capability_override and is_system_workspace_agent(agent_id, spec.owner_workspace):
        # The system workspace rule is never softened (docs/system-workspace.md).
        raise HTTPException(status_code=400, detail="A system workspace agent cannot carry a capability override")
    new_spec = dataclasses.replace(spec, capability_override=bool(data.capability_override))
    try:
        registry.add_agent(new_spec)
    except CapabilityViolation as e:
        raise _capability_conflict(e)
    return _capability_override_state(new_spec)


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
        raise _capability_conflict(e)
    # Read back: for a child (extends) the saved overrides and deltas differ
    # from the ones the edited spec carried.
    new_spec = registry.get_agent(agent_id) or new_spec
    return {**new_spec.to_dict(), "capability_warning": _capability_warning_dict(new_spec)}


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
    try:
        # The guard walks the delegation graph, so a delegate that brings the
        # missing third of the trifecta is refused here exactly like a tool.
        registry.add_agent(new_spec)
    except CapabilityViolation as e:
        raise _capability_conflict(e)
    return {**new_spec.to_dict(), "capability_warning": _capability_warning_dict(new_spec)}


def _validated_handoffs(agent_id: str, ids: List[str]) -> List[str]:
    """Handoff targets, stripped and de-duplicated; 400 for the agent itself
    or an id the registry does not know. Whether a target is usable in a
    workspace is checked when the handoff happens, since one agent record
    serves every workspace it is visible in."""
    cleaned: List[str] = []
    for raw in ids or []:
        hid = str(raw or "").strip()
        if not hid or hid in cleaned:
            continue
        if hid == agent_id:
            raise HTTPException(status_code=400, detail="An agent cannot hand the conversation to itself")
        if registry.get_agent(hid) is None:
            raise HTTPException(status_code=400, detail=f"Agent '{hid}' does not exist")
        cleaned.append(hid)
    return cleaned


def _validated_handoff_history(value: Any) -> str:
    """The canonical history filter, or 400 naming the allowed values."""
    if registry.parse_handoff_history(value) is None:
        raise HTTPException(
            status_code=400,
            detail=(f"handoff_history must be full, summary, none or last_n:<N> "
                    f"(N from 1 to {registry.HANDOFF_LAST_N_MAX}), not '{value}'"),
        )
    return registry.normalize_handoff_history(value)


def _handoffs_dict(spec: Any) -> Dict[str, Any]:
    return {"handoffs": list(spec.handoffs or []), "handoff_history": spec.handoff_history or "full"}


@router.get("/{agent_id}/handoffs")
async def get_agent_handoffs(agent_id: str):
    """The agents this one may hand the conversation to, and the history the
    receiver sees by default (docs/handoffs.md)."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _handoffs_dict(spec)


@router.post("/{agent_id}/handoffs")
async def update_agent_handoffs(agent_id: str, data: AgentHandoffsUpdate, request: Request):
    """Set the handoff targets and/or the default history filter. A field left
    out (null) keeps its value; an empty list removes the handoff tool."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    changes: Dict[str, Any] = {}
    if data.handoffs is not None:
        changes["handoffs"] = _validated_handoffs(agent_id, data.handoffs)
    if data.handoff_history is not None:
        changes["handoff_history"] = _validated_handoff_history(data.handoff_history)
    new_spec = dataclasses.replace(spec, **changes) if changes else spec
    try:
        registry.add_agent(new_spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        from common import audit, identity
        audit.record("agent.handoffs", principal=identity.request_principal(request),
                     object_type="agent", object_id=agent_id,
                     workspace=getattr(new_spec, "owner_workspace", None),
                     ip=identity.client_ip(request), details=changes)
    except Exception:  # noqa: BLE001 - an audit failure must not undo the save
        import logging
        logging.getLogger(__name__).warning(
            "could not record the handoffs audit entry for %s", agent_id, exc_info=True)
    return _handoffs_dict(new_spec)


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

    # dataclasses.replace, not a field-by-field rebuild: the rebuild dropped
    # every field added to AgentSpec after it was written (delegates, handoffs,
    # the loop policies, secrets, ...), the bug routes/agents.py's tools route
    # already fixed the same way.
    new_spec = dataclasses.replace(spec, reasoning=current)
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

    # dataclasses.replace, not a field-by-field rebuild: the rebuild dropped
    # every field added to AgentSpec after it was written (delegates, handoffs,
    # the loop policies, secrets, ...), the bug routes/agents.py's tools route
    # already fixed the same way.
    new_spec = dataclasses.replace(
        spec,
        provider=new_provider,
        model=new_model,
        base_url=new_base_url,
        temperature=new_temperature,
        max_tokens=new_max_tokens,
        api_key=new_api_key,
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
async def create_custom_agent(data: AgentCreateCustom, request: Request):
    """Create a new agent: register structured fields and write instructions.md.

    With ``extends`` the new agent is a child (agents/inheritance.py): every
    field not given is inherited from the parent, ``system_prompt`` is
    optional and holds only the child's own additions, and ``tools``, when
    given, is the child's full effective tool list."""
    if registry.get_agent(data.id) is not None:
        raise HTTPException(status_code=400, detail=f"Agent '{data.id}' already exists")
    # Checked before anything is written, so a bad target leaves no folder behind.
    handoffs = _validated_handoffs(data.id, data.handoffs)
    handoff_history = _validated_handoff_history(data.handoff_history or "full")

    factory = get_factory()
    extends = (data.extends or "").strip() or None

    # When definition_id is supplied, reuse an existing shared definition folder
    # instead of authoring a new one (system_prompt is ignored).
    definition_id = (data.definition_id or "").strip() or None
    if extends and definition_id:
        raise HTTPException(status_code=400,
                            detail="An agent that extends another cannot share a definition (definition_id).")

    # Agents created inside a (non-default) workspace are owned by — and only
    # visible in — that workspace until they are explicitly shared.
    owner_workspace = (data.workspace or "").strip() or None
    if owner_workspace == "default":
        owner_workspace = None

    from common import identity
    owner_user = getattr(identity.request_principal(request), "id", None)

    if extends:
        from agents import inheritance
        parent_eff = inheritance.parent_effective(extends, data.extends_version)
        if parent_eff is None:
            raise HTTPException(status_code=400, detail=f"Parent agent '{extends}' not found.")
        given = data.model_fields_set
        own: Dict[str, Any] = {}
        if "tools" in given:
            own["tools"] = list(data.tools)
        if "handoffs" in given:
            own["handoffs"] = handoffs
        if "handoff_history" in given:
            own["handoff_history"] = handoff_history
        spec = inheritance.new_child_spec(
            parent_eff,
            extends_version=data.extends_version,
            id=data.id,
            name=data.name,
            description=data.description,
            domain=data.domain,
            capacity=data.capacity,
            owner_workspace=owner_workspace,
            owner_user=owner_user,
            **own,
        )
        # The registry's rules first, so a refused parent leaves no folder behind.
        try:
            inheritance.validate_extends(spec)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        prompt_assembly.write_instructions(
            data.id, data.system_prompt or "", definitions_dir=factory.definitions_dir
        )
    else:
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
            handoffs=handoffs,
            handoff_history=handoff_history,
            owner_user=owner_user,
        )
    try:
        registry.add_agent(spec)
    except CapabilityViolation as e:
        if extends:
            prompt_assembly.delete_definition(data.id, definitions_dir=factory.definitions_dir)
        raise HTTPException(
            status_code=409,
            detail={"error": "capability_violation", **e.violation.to_dict()},
        )
    except ValueError as e:
        if extends:
            prompt_assembly.delete_definition(data.id, definitions_dir=factory.definitions_dir)
        raise HTTPException(status_code=400, detail=str(e))
    if extends:
        spec = registry.get_agent(data.id) or spec

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
    # A child (extends) cannot share a definition folder: the copy extends the
    # same parent and gets its own copy of the child's own text instead.
    new_spec = dataclasses.replace(
        source,
        id=new_id,
        definition_id=None if source.extends else def_id,
        owner_workspace=owner_workspace,
        shared=False,
        is_default_chat_agent=False,
    )
    if source.extends:
        factory = get_factory()
        prompt_assembly.write_instructions(
            new_id, prompt_assembly.read_instructions(def_id, factory.definitions_dir),
            definitions_dir=factory.definitions_dir)
        for reader, writer in ((prompt_assembly.read_capabilities, prompt_assembly.write_capabilities),
                               (prompt_assembly.read_usage, prompt_assembly.write_usage)):
            text = reader(def_id, factory.definitions_dir)
            if text:
                writer(new_id, text, definitions_dir=factory.definitions_dir)
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

    # dataclasses.replace, not a field-by-field rebuild: the rebuild dropped
    # every field added to AgentSpec after it was written (delegates, handoffs,
    # the loop policies, secrets, ...), the bug routes/agents.py's tools route
    # already fixed the same way.
    new_spec = dataclasses.replace(spec, shared=data.shared)
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


# Agent inheritance (extends): GET /{id}/inheritance, PUT /{id}/extends and
# DELETE /{id}/overrides/{field} live in their own module, included here so
# main.py stays as it is.
from routes.agent_inheritance import router as _inheritance_router  # noqa: E402

router.include_router(_inheritance_router)
