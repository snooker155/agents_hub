"""
One name per kind on the marketplace.

The marketplace (routes/marketplace.py) lists what workspaces published:
agents, flows and skills. Two published items of the same kind under one name
cannot be told apart there, so publishing (or renaming a published item to) a
name another published item of that kind already carries is refused. Names
compare without case and with runs of spaces folded, so "Code Review" and
"code  review" are the same name. Unpublished items may share names freely:
each lives in its own workspace.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def name_key(name: Any) -> str:
    """The form two names are compared in."""
    return " ".join(str(name or "").split()).casefold()


def _review_required() -> bool:
    """Whether the hub requires a review before a shared item is listed
    (``AGENTS_HUB_REGISTRY_REQUIRE_REVIEW``, agents/registry.py). Off, sharing
    alone is enough, exactly as before the review existed."""
    from agents import registry
    return bool(registry.registry_review_required())


def is_marketplace_agent(spec: Any) -> bool:
    """An agent is listed on the marketplace when explicitly published and,
    when the hub requires review, approved."""
    from workspace import is_system_agent
    if is_system_agent(spec.id) or getattr(spec, "default_workspace_only", False):
        return False
    if not getattr(spec, "shared", False):
        return False
    if _review_required() and getattr(spec, "review_status", None) != "approved":
        return False
    return True


def is_marketplace_flow(flow: Dict[str, Any]) -> bool:
    """A flow is listed on the marketplace when shared and, when the hub
    requires review (common/review.py, shared with agents and skills), approved."""
    if not flow.get("shared"):
        return False
    if _review_required() and flow.get("review_status") != "approved":
        return False
    return True


def published_agent_named(name: Any, *, exclude: Iterable[str] = ()) -> Optional[Any]:
    """The published agent called ``name``, other than the ids in ``exclude``."""
    from agents import registry
    key, skip = name_key(name), set(exclude)
    if not key:
        return None
    for spec in registry.list_agents():
        if spec.id not in skip and is_marketplace_agent(spec) and name_key(spec.name) == key:
            return spec
    return None


def published_flow_named(name: Any, *, exclude: Iterable[str] = ()) -> Optional[Dict[str, Any]]:
    """The published flow called ``name``, other than the ids in ``exclude``."""
    from flow import store as flow_store
    key, skip = name_key(name), set(exclude)
    if not key:
        return None
    for flow in flow_store.list_flows():
        if flow.get("id") not in skip and is_marketplace_flow(flow) and name_key(flow.get("name")) == key:
            return flow
    return None


def published_skill_named(name: Any, *, exclude: Iterable[str] = ()) -> Optional[Any]:
    """The published skill called ``name``, other than the ids in ``exclude``."""
    from memory.procedural import all_procedures
    key, skip = name_key(name), {str(i) for i in exclude}
    if not key:
        return None
    for procedure in all_procedures():
        if str(procedure.id) not in skip and procedure.shared and name_key(procedure.name) == key:
            return procedure
    return None


def agent_name_taken(name: Any, *, exclude: Iterable[str] = ()) -> Optional[str]:
    """``None`` when an agent may be published as ``name``, else why not."""
    other = published_agent_named(name, exclude=exclude)
    if other is None:
        return None
    return (f"An agent named '{other.name}' is already published to the marketplace "
            f"(from workspace '{other.owner_workspace or 'default'}'); rename this one first")


def flow_name_taken(name: Any, *, exclude: Iterable[str] = ()) -> Optional[str]:
    """``None`` when a flow may be published as ``name``, else why not."""
    other = published_flow_named(name, exclude=exclude)
    if other is None:
        return None
    return (f"A flow named '{other.get('name')}' is already published to the marketplace "
            f"(from workspace '{other.get('workspace') or 'default'}'); rename this one first")


def skill_name_taken(name: Any, *, exclude: Iterable[str] = ()) -> Optional[str]:
    """``None`` when a skill may be published as ``name``, else why not."""
    other = published_skill_named(name, exclude=exclude)
    if other is None:
        return None
    return (f"A skill named '{other.name}' is already published to the marketplace "
            f"(from workspace '{other.workspace or 'default'}'); rename this one first")


__all__ = [
    "agent_name_taken", "flow_name_taken", "is_marketplace_agent", "is_marketplace_flow",
    "name_key", "published_agent_named", "published_flow_named", "published_skill_named",
    "skill_name_taken",
]
