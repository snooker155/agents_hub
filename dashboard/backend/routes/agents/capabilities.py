"""Capability overrides, tools, delegates, handoffs, reasoning, model and default chat agent."""
import dataclasses

from fastapi import APIRouter, HTTPException, Request
from typing import Any, Dict, List, Optional

from agents import registry
from agents.capability_guard import (
    CapabilityViolation, capability_warning, guard_mode, is_system_workspace_agent,
    override_honoured_at_build, override_requires_container,
)
from models import AgentToolsUpdate, AgentDelegatesUpdate, AgentHandoffsUpdate, AgentModelUpdate, AgentReasoningUpdate, AgentCapabilityOverrideUpdate
from workspace import system_agent_ids



from ._common import (package, _get_workspace_default_chat_agent, _set_workspace_default_chat_agent, _workspace_for_chat_default)

router = APIRouter(prefix="/api/agents", tags=["agents"])


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


def _can_edit_agent(request: Request, spec) -> bool:
    """Whether the caller holds editor on the agent's workspace (always true
    outside multi): the chat's refusal card shows its button only to them."""
    from common import identity
    from common.auth import WS_EDITOR
    try:
        identity.require_role(identity.request_principal(request),
                              workspace=getattr(spec, "owner_workspace", None) or "default", role=WS_EDITOR)
    except HTTPException:
        return False
    return True


@router.get("/{agent_id}/capability-override")
async def get_agent_capability_override(request: Request, agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {**_capability_override_state(spec), "can_edit": _can_edit_agent(request, spec)}


@router.post("/{agent_id}/capability-override")
async def update_agent_capability_override(request: Request, agent_id: str, data: AgentCapabilityOverrideUpdate):
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
    if not _can_edit_agent(request, spec):
        raise HTTPException(status_code=403, detail="Editor access to the agent's workspace is required")
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
    metadata = package().get_workspace_metadata(ws_name)
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

