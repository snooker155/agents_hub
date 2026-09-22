"""The Planner agent: its prompt, and how it is built and run.

Split out of ``routes/projects.py``. The run-record bookkeeping, the chat-log
scaffold and the SSE queue plumbing around one planner turn are shared with
the project graph (architect) chat and stay in the route; this module is the
two things that are actually planner-specific — the prompt, and the agent
itself.
"""
from __future__ import annotations

from typing import List, Optional

#: The Planner system agent's id — also the store key for its run records.
PLANNER_AGENT_ID = "planner"


def ensure_planner_agent() -> bool:
    """Ensure the Planner agent is registered, backfilling it on installs that
    predate it. Returns False if it cannot be made available."""
    try:
        from agents.registry import get_agent
        if get_agent(PLANNER_AGENT_ID) is not None:
            return True
        from common.bootstrap import _ensure_system_agents
        _ensure_system_agents()
        return get_agent(PLANNER_AGENT_ID) is not None
    except Exception:
        return False


def build_prompt(history: List[dict], user_message: str) -> str:
    """One turn's prompt: the recent conversation, then the instruction.

    The task tree and the project's structure graphs are not inlined — the
    agent reads them live with its own tools (``list_tasks``,
    ``get_project_graph``) — so the prompt only needs to carry the talk and
    the ask. ``history`` is expected with the user's new message already
    dropped (the caller records it separately); the last 12 turns are enough
    for "also split X" / "reprioritise Y" follow-ups to have context.
    """
    convo = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in history[-12:])
    return (
        "You are the project's task planner: read its structure graphs and manage the "
        "task tree in the shared tracker.\n\n"
        "Rules:\n"
        "1. Call list_tasks first to see what already exists — extend/refine it, do not duplicate.\n"
        "2. Call get_project_graph for view='process' AND for view='architecture' to read both graphs.\n"
        "3. Create top-level tasks for the major components/stages with create_task, break them into "
        "steps with add_subtask, and use create_sequence for work that must run in order. Align task "
        "titles with the project's structure. For a refinement request, change only what's asked.\n\n"
        f"--- CONVERSATION SO FAR ---\n{convo or '(none)'}\n\n"
        f"--- USER REQUEST ---\n{user_message}\n\n"
        "After applying the changes, reply with ONE short sentence summarising what you did."
    )


def build_agent(workspace_path: Optional[str]):
    """Create the Planner agent.

    No repetition ceiling (``max_tool_repeats=0``): planning a project is task
    after task through the same tool, which is the work the agent exists to
    do, not a runaway loop. ``max_iterations=400`` keeps a large plan from
    being cut off mid-way.
    """
    from agents.agent_factory import create_agent
    return create_agent(
        PLANNER_AGENT_ID, workspace=workspace_path, streaming=True,
        max_tool_repeats=0, max_iterations=400,
    )


__all__ = ["PLANNER_AGENT_ID", "ensure_planner_agent", "build_prompt", "build_agent"]
