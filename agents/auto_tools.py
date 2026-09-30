"""
The tools an agent gets at build time on top of its record.

``agents.agent_factory._build_agent`` appends tools the operator never picked
in the tool list: the handoff tool once handoff targets exist, the reasoning
scratchpad and plan store once think or plan are on, skills tools when skills
are enabled, ``ask_user`` behind the clarification gate, the shared-memory
tools once a pool is bound. The Tools tab shows them as read-only cards so the
page lists everything a run will actually hold, each with the setting that
brought it. This module mirrors those conditions without building the agent
(no model client, no MCP connect); when the factory gains an injection, add it
here too.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Descriptions for tools that are not in the catalog (tools/registry.py), so
# the card has something to say without instantiating the tool.
_EXTRA_DESCRIPTIONS: Dict[str, str] = {
    "handoff_to_agent": "Give the conversation to one of the agents listed as handoff targets; that agent answers the user and keeps the conversation.",
    "think": "A private scratchpad for reasoning before acting.",
    "plan": "Draft a step plan for the current request.",
    "assess_complexity": "Decide whether the request needs a plan at all.",
    "recall": "Search the shared memory pool: slots, notes, the knowledge graph and documents.",
    "remember": "Store a fact, a structured record or a note in the primary pool.",
    "forget": "Delete a slot or a note from the primary pool.",
    "record_episode": "Record what happened in this run as an episode.",
    "recall_episodes": "Search earlier episodes.",
    "link": "Connect two things in the knowledge graph.",
    "traverse": "Walk the knowledge graph from an entity.",
    "memory_block_read": "Read a memory block by name.",
    "memory_block_replace": "Replace the contents of a memory block.",
    "memory_block_append": "Append to a memory block.",
    "extract_from_text": "Extract structured facts from a text.",
    "save_extraction": "Save extracted facts to the pool.",
}


def _entry(tool_id: str, reason: str, *, own: List[str]) -> Optional[Dict[str, Any]]:
    """One card, or None when the tool is already on the record (then it is
    an ordinary, toggleable tool and the operator sees it in its group)."""
    if tool_id in own:
        return None
    try:
        from tools.registry import get_tool_by_id
        spec = get_tool_by_id(tool_id)
    except Exception:  # noqa: BLE001 - a catalog hiccup only loses the label
        spec = None
    label = spec.name if spec else tool_id.replace("_", " ").strip().title()
    description = (spec.description if spec else "") or _EXTRA_DESCRIPTIONS.get(tool_id, "")
    category = spec.category if spec else "other"
    try:
        from tools.capabilities import grants_of
        capabilities = sorted(grants_of(tool_id))
    except Exception:  # noqa: BLE001
        capabilities = []
    return {
        "id": tool_id, "label": label, "description": description,
        "category": category, "capabilities": capabilities, "reason": reason,
    }


def auto_injected_tools(spec: Any, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """The tools the factory would add to *spec* in *workspace*, in the order
    the factory adds them, each with a ``reason`` key naming the setting
    (``handoffs``, ``think``, ``plan``, ``skills``, ``clarify_gate``,
    ``memory_pool``, ``project``). Tools already on the record are left out."""
    own = [str(t) for t in (getattr(spec, "tools", None) or [])]
    out: List[Dict[str, Any]] = []

    def add(tool_id: str, reason: str) -> None:
        e = _entry(tool_id, reason, own=own)
        if e is not None and all(x["id"] != tool_id for x in out):
            out.append(e)

    # Shared memory pool (memory/injection.py): the conversational memory
    # tools once a pool is bound in this workspace, its own or the user's
    # personal one (memory/personal.py), the write tool for episodes only
    # when episodic write resolves on.
    try:
        from memory.binding import effective_memory_pools
        pools = effective_memory_pools(spec, workspace, None)
    except Exception:  # noqa: BLE001 - no pool store, no pool tools
        pools = []
    if not pools:
        try:
            from memory import personal
            pools = ["personal"] if personal.enabled(spec, workspace) else []
        except Exception:  # noqa: BLE001 - unreadable settings read as off
            pools = []
    if pools:
        try:
            from agents.agent_factory import resolve_episodic_write
            provider = str((getattr(spec, "config", None) or {}).get("provider") or "")
            episodic = resolve_episodic_write(getattr(spec, "episodic_write_enabled", None), provider or None)
        except Exception:  # noqa: BLE001
            episodic = True
        names = ["recall", "remember", "forget", "recall_episodes", "link", "traverse",
                 "memory_block_read", "memory_block_replace", "memory_block_append"]
        if episodic:
            names.insert(3, "record_episode")
        for n in names:
            add(n, "memory_pool")

    # Skills (memory/procedural.py): get_skill and create_skill when skills
    # are on; read_skill_file only for an agent with a skill that has files.
    if getattr(spec, "skills_enabled", False):
        add("get_skill", "skills")
        add("create_skill", "skills")
        try:
            from memory.procedural import ProcedureStore
            from common.workspace_context import normalize_workspace_name
            ws = normalize_workspace_name(workspace)
            if ws and any(p.resources for p in ProcedureStore(ws).load() if p.agent_id == spec.id):
                add("read_skill_file", "skills")
        except Exception:  # noqa: BLE001 - no procedure store: the tool is simply not granted
            log.debug("auto_tools: could not read the procedures of %s", spec.id, exc_info=True)

    # Clarification gate grants ask_user.
    if getattr(spec, "clarify_gate", False):
        add("ask_user", "clarify_gate")

    # Reasoning (reasoning/config.py): think, and the plan scratchpad plus the
    # persistent plan store when planning is on.
    try:
        from reasoning.config import resolve_reasoning
        resolved = resolve_reasoning(getattr(spec, "reasoning", None), own)
    except Exception:  # noqa: BLE001
        resolved = {}
    if resolved.get("think_enabled"):
        add("think", "think")
    if resolved.get("plan_enabled"):
        for n in ("assess_complexity", "plan", "save_plan", "get_plan", "list_plans",
                  "update_plan_status", "delete_plan"):
            add(n, "plan")

    # Conversation handoff, only with targets (tools/handoff.py).
    if getattr(spec, "handoffs", None):
        add("handoff_to_agent", "handoffs")

    # Project-scoped runs only: listed so the operator knows it exists, the
    # reason says when.
    add("get_project_graph", "project")
    return out
