from __future__ import annotations

from typing import Any, Dict


def inject_memory_into_definition(agent_id: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    """Inject shared memory pool context into an agent definition dict.

    Reads memory_type/memory_data from the registry spec for agent_id.
    When a shared pool is configured, appends a ## Shared Memory Pool section
    to the system prompt and ensures read_memory is in the tool list.

    Returns the (possibly mutated) definition dict.
    """
    from agents.registry import get_agent as _reg_get
    spec = _reg_get(agent_id)
    if not (spec and spec.memory_type == "shared" and spec.memory_data):
        return definition

    pool_id = str(spec.memory_data)
    try:
        from memory.store import MemoryStore
        pool = MemoryStore().get(pool_id)
        pool_label = f'"{pool.name}"' if pool else pool_id
        file_hint = (
            f" It contains the following files: {', '.join(f['name'] for f in pool.files)}."
            if pool and pool.files else ""
        )
    except Exception:
        pool_label = pool_id
        file_hint = ""

    tool_list = list(definition.get("tools") or [])
    if "read_memory" not in tool_list:
        tool_list.append("read_memory")
    can_write = "write_memory" in tool_list

    # Add semantic search tool when vector store is configured
    from memory.rag_query import is_rag_configured
    has_rag = is_rag_configured()
    if has_rag and "search_memory" not in tool_list:
        tool_list.append("search_memory")

    write_hint = (
        f" Use the `write_memory` tool with `memory_id=\"{pool_id}\"` to create or update files, notes, "
        f"and key-value pairs in the pool."
        if can_write else ""
    )
    search_hint = (
        f" Use the `search_memory` tool with `memory_id=\"{pool_id}\"` to semantically search the pool "
        f"and retrieve the most relevant chunks for any query."
        if has_rag else ""
    )

    memory_block = (
        f"\n\n## Shared Memory Pool\n"
        f"You have access to a shared memory pool {pool_label} (ID: `{pool_id}`)."
        f"{file_hint}\n"
        f"Use the `read_memory` tool with `memory_id=\"{pool_id}\"` to list or read files, notes, and key-value pairs "
        f"whenever the task may benefit from that context."
        f"{search_hint}"
        f"{write_hint}"
    )
    definition["system_prompt"] = (definition.get("system_prompt") or "") + memory_block
    definition["tools"] = tool_list

    return definition
