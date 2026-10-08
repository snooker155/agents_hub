"""Which memory pool a tool call may reach: ownership and workspace scope checks."""
from __future__ import annotations

import logging
from typing import Optional


from .store import MemoryStore

log = logging.getLogger(__name__)


def _own_pool(store: MemoryStore, memory_id: str):
    """The pool ``memory_id`` names, or None when it does not exist or is
    another user's personal memory (memory/personal.py): to the caller, a pool
    it may not read looks the same as one that is not there."""
    mem = store.get(memory_id)
    if mem is None:
        return None
    from memory.personal import is_foreign
    if is_foreign(mem):
        return None
    return mem if _pool_in_scope(mem) else None


def _bound_pool_ids(agent_id: str, workspace: str) -> Optional[set]:
    """The pools ``agent_id`` is bound to in ``workspace``: its own assignment
    there (memory/binding.py ``effective_memory_pools``) and, when personal
    memory is on for it, the current user's personal pool of that workspace.
    None when the agent is not in the registry."""
    from agents.registry import get_agent
    spec = get_agent(agent_id)
    if spec is None:
        return None
    from memory.binding import effective_memory_pools
    ids = {str(p) for p in effective_memory_pools(spec, workspace)}
    try:
        from memory import personal
        if personal.enabled(spec, workspace):
            ids.add("personal")  # marker: the user's own personal pool, checked below
    except Exception:  # noqa: BLE001 - personal memory unreadable: own pools only
        log.debug("memory: personal pool unreadable for this agent", exc_info=True)
    return ids


def _pool_in_scope(mem) -> bool:
    """Whether the running agent may read and write the pool ``mem``
    (common/workspace_scope.py): only the pools its run is bound to in this
    workspace. The tools that take a ``memory_id`` from the model check it
    here, so an id of another workspace's pool, or of a pool of this
    workspace the agent is not bound to, is answered like a missing one.
    The service's own agents reach every pool; with no workspace at all (the
    CLI) every pool is reachable, as before. An unknown agent (a bare tool
    call inside a workspace) reaches the pools of that workspace."""
    from common.workspace_scope import (
        check_record, current_agent, current_workspace, is_service_wide, same_workspace,
    )
    agent = current_agent()
    if is_service_wide(agent):
        return True
    ws = current_workspace()
    if not ws:
        return True
    bound = _bound_pool_ids(agent, ws) if agent else None
    if bound is None:
        return check_record(getattr(mem, "workspace", None), what="memory pool", workspace=ws) is None
    if str(mem.id) in bound:
        return True
    from memory.personal import is_personal
    return ("personal" in bound and is_personal(mem)
            and same_workspace(getattr(mem, "workspace", None), ws))
