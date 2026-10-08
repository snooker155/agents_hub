"""Creating, cloning and sharing agents."""
import re
import dataclasses

from fastapi import APIRouter, HTTPException, Request
from typing import Any, Dict

from agents import registry
from agents import prompt_assembly
from agents.capability_guard import (
    CapabilityViolation,
)
from models import AgentCreateCustom, AgentCloneToWorkspace, AgentSharingUpdate



from ._common import (package, get_factory)
from .capabilities import _validated_handoff_history, _validated_handoffs

router = APIRouter(prefix="/api/agents", tags=["agents"])


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
            package().create_workspace_folder(owner_workspace)
            metadata = package().get_workspace_metadata(owner_workspace)
            allowed = list(metadata.get("allowed_agents") or [])
            if data.id not in allowed:
                allowed.append(data.id)
                package().update_workspace_metadata(owner_workspace, {"allowed_agents": allowed})
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
            package().create_workspace_folder(owner_workspace)
            metadata = package().get_workspace_metadata(owner_workspace)
            allowed = list(metadata.get("allowed_agents") or [])
            if new_id not in allowed:
                allowed.append(new_id)
                package().update_workspace_metadata(owner_workspace, {"allowed_agents": allowed})
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

