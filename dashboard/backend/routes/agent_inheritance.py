"""
Agent inheritance routes (agents/inheritance.py, docs/agent-inheritance.md).

* ``GET /api/agents/{id}/inheritance``: the chain, the children, every
  inheritable field with its value and the agent that set it, list deltas,
  effective lists, and the prompt split into inherited and own sections.
* ``PUT /api/agents/{id}/extends``: set, change or repin the parent;
  ``extends: null`` detaches, making the effective setup and prompt the
  agent's own.
* ``DELETE /api/agents/{id}/overrides/{field}``: one field back to inherited.

Included from the bottom of routes/agents.py (``router.include_router``), so
it shares the ``/api/agents`` prefix and main.py is left alone.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from agents import inheritance, prompt_assembly, registry
from agents.agent_factory import get_factory
from agents.capability_guard import CapabilityViolation, InheritedCapabilityViolation, guard_mode
from models import AgentExtendsUpdate

router = APIRouter()


def _conflict(e: CapabilityViolation) -> HTTPException:
    if isinstance(e, InheritedCapabilityViolation):
        return HTTPException(status_code=409, detail={"guard_mode": guard_mode(), **e.detail()})
    return HTTPException(status_code=409, detail={
        "error": "capability_violation", "guard_mode": guard_mode(), **e.violation.to_dict(),
    })


def _describe(agent_id: str) -> Dict[str, Any]:
    data = inheritance.describe(agent_id, definitions_dir=get_factory().definitions_dir)
    if data is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return data


@router.get("/{agent_id}/inheritance")
async def get_agent_inheritance(agent_id: str):
    """Where every inheritable field of the agent comes from (see module docstring)."""
    return _describe(agent_id)


def _detach(agent_id: str) -> None:
    """Make the effective spec and prompt the agent's own."""
    effective = registry.get_agent(agent_id)
    if effective is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    defs_dir = get_factory().definitions_dir
    parts = inheritance.effective_parts(effective, definitions_dir=defs_dir)
    own = dataclasses.replace(effective, extends=None, extends_version=None, overrides=[], list_deltas={})
    registry.add_agent(own, actor="dashboard", note="detached from its parent")
    def_id = own.def_id()
    prompt_assembly.write_instructions(def_id, parts["instructions"], definitions_dir=defs_dir)
    for name, writer in (("capabilities", prompt_assembly.write_capabilities),
                         ("usage", prompt_assembly.write_usage)):
        if parts[name].strip():
            writer(def_id, parts[name], definitions_dir=defs_dir)
        else:
            prompt_assembly.clear_part(def_id, getattr(prompt_assembly, f"{name.upper()}_FILE"), defs_dir)


@router.put("/{agent_id}/extends")
async def set_agent_extends(agent_id: str, data: AgentExtendsUpdate):
    """Set, change or repin the parent, or detach with ``extends: null``.

    * A standalone agent getting a parent keeps behaving as it does: every
      field equal to the parent's becomes inherited, the rest overrides.
    * A child changing parent or pin keeps its own overrides and deltas,
      now applied to the new parent (or version).
    * Detaching writes the effective setup and prompt as the agent's own.
    """
    raw = registry.get_agent_raw(agent_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    extends = (data.extends or "").strip() or None
    try:
        if extends is None:
            if raw.extends:
                _detach(raw.id)
        elif raw.extends:
            registry.save_raw(
                dataclasses.replace(raw, extends=extends, extends_version=data.extends_version),
                actor="dashboard", note=f"parent set to {extends}"
                + (f"@{data.extends_version}" if data.extends_version is not None else ""),
            )
        else:
            effective = registry.get_agent(raw.id)
            if effective is None:
                raise HTTPException(status_code=404, detail="Agent not found")
            registry.add_agent(
                dataclasses.replace(effective, extends=extends, extends_version=data.extends_version),
                actor="dashboard", note=f"now extends {extends}",
            )
    except CapabilityViolation as e:
        raise _conflict(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _describe(raw.id)


@router.delete("/{agent_id}/overrides/{field_name}")
async def reset_agent_override(agent_id: str, field_name: str):
    """Reset one field to inherited: a scalar or dict field drops the agent's
    own value, a list field its add and remove deltas."""
    raw = registry.get_agent_raw(agent_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not raw.extends:
        raise HTTPException(status_code=400, detail=f"Agent '{agent_id}' does not extend another agent.")
    if field_name not in inheritance.INHERITABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"'{field_name}' is not an inheritable field.")
    try:
        registry.save_raw(inheritance.reset_override(raw, field_name),
                          actor="dashboard", note=f"{field_name} reset to inherited")
    except CapabilityViolation as e:
        raise _conflict(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _describe(raw.id)


__all__ = ["router"]
