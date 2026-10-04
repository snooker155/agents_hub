"""Workspace-scoped resolution of an agent's shared memory assignment.

Agent records can be shared across workspaces (a workspace adds the agent id
to its ``allowed_agents`` list), but each workspace works with its own logical
instance of the agent. A memory pool assigned in one workspace must therefore
not leak into another.

Resolution rule:
- In the agent's home workspace (``owner_workspace``, or ``default`` when the
  record is not bound to one), the record-level ``memory_type``/``memory_data``
  applies — this keeps existing single-workspace setups working unchanged.
- In any other workspace, the assignment lives in that workspace's metadata
  under ``agent_memory_overrides[agent_id] = {"memory_type", "memory_data"}``;
  no entry means the agent has no shared memory there.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple


def home_workspace(spec) -> str:
    """The workspace whose assignment is stored on the agent record itself."""
    return spec.owner_workspace or "default"


def workspace_memory_override(agent_id: str, workspace: str) -> Optional[dict]:
    """The raw per-workspace assignment for agent_id, or None when unset."""
    try:
        from workspace import get_workspace_metadata
        overrides = get_workspace_metadata(workspace).get("agent_memory_overrides") or {}
        entry = overrides.get(agent_id)
        return dict(entry) if isinstance(entry, dict) else None
    except Exception:
        return None


def effective_memory(spec, workspace: Optional[str] = None) -> Tuple[str, Any]:
    """(memory_type, memory_data) effective for this agent in this workspace.

    ``workspace`` may be a name or a path; None falls back to 'default'.
    """
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace) or "default"
    if ws == home_workspace(spec):
        return spec.memory_type, spec.memory_data
    override = workspace_memory_override(spec.id, ws)
    if override:
        return override.get("memory_type") or "none", override.get("memory_data")
    return "none", None


def effective_read_only_pools(spec, workspace: Optional[str] = None) -> frozenset:
    """Pool ids effective for *spec* in *workspace* whose binding is marked
    read only (``agents.registry.memory_pool_read_only_ids``).

    Resolved the same way as :func:`effective_memory`: the record's own
    assignment in the home workspace, the workspace override elsewhere. The
    Memory page's ``pool_override`` (effective_memory_pools' own parameter) is
    deliberately not covered here: pinning a pool to look at it while
    answering a question is not the agent's own binding, so it stays
    writable regardless of what the binding says.
    """
    memory_type, memory_data = effective_memory(spec, workspace)
    from agents.registry import memory_pool_read_only_ids
    return memory_pool_read_only_ids(memory_type, memory_data)


def effective_memory_pools(spec, workspace: Optional[str] = None,
                           pool_override: Optional[Any] = None,
                           personal_pool: Optional[str] = None) -> list:
    """Shared memory pool ids (primary first) effective in this workspace.

    ``personal_pool`` is the user's personal pool (memory/personal.py), put
    after the agent's own pools: with none of its own it is the only one and
    so the primary; next to them it is searched too and written to on
    request (``remember(personal=True)``, memory/tool.py).

    ``pool_override`` pins the assignment for one build, ignoring both the
    record and the workspace. It exists for surfaces where the pool is part of
    what the user is looking at rather than part of the agent's configuration:
    the Memory page binds the agent to the pool open on screen, so a question
    asked about that pool is answered from it. Callers that pass it must also
    let it reach the agent cache key, or the next build would hand back an
    agent bound to the previous pool.
    """
    from agents.registry import normalize_memory_pools
    if pool_override:
        pools = normalize_memory_pools("shared", pool_override)
    else:
        memory_type, memory_data = effective_memory(spec, workspace)
        pools = normalize_memory_pools(memory_type, memory_data)
    personal_pool = str(personal_pool or "").strip()
    if personal_pool and personal_pool not in pools:
        pools.append(personal_pool)
    return pools


#: The memory tools that change a pool. A run whose task binds pools read-only
#: (``Task.memory_access == "read"``, set by a deployment) is built without
#: them; everything else of the memory tool set stays (memory/tool.py,
#: memory/knowledge_extract.py).
MEMORY_WRITE_TOOLS = frozenset({
    "write_memory", "write_structured_memory", "append_journal",
    "memory_block_replace", "memory_block_append",
    "remember", "forget", "record_episode", "link",
    "save_extraction",
})

MEMORY_ACCESS_MODES = ("read", "write")
