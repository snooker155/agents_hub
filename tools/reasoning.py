"""Reasoning (think / plan) capability config and prompt assembly.

The per-agent ``reasoning`` dict on an :class:`AgentSpec` is the source of
truth for *whether* the think/plan scratchpad tools are available and *how*
the agent should use them. Shape::

    {
        "think_enabled": bool,   # add the think tool
        "think_mode":    str,    # standard | deep | analytical
        "plan_enabled":  bool,   # add the plan tool
        "plan_format":   str,    # structured | bullet | numbered | freeform
    }

Older configs only carried ``think_mode`` / ``plan_format`` and relied on the
tools list to enable the tools. :func:`resolve_reasoning` normalises both
shapes, inferring the ``*_enabled`` flags from a legacy tools list when the
explicit flags are absent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


DEFAULT_THINK_MODE = "standard"
DEFAULT_PLAN_FORMAT = "structured"


def resolve_reasoning(
    reasoning: Optional[Dict[str, Any]],
    tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Normalise a raw ``reasoning`` dict into explicit, defaulted fields.

    When ``think_enabled`` / ``plan_enabled`` are missing (legacy config), fall
    back to whether ``think`` / ``plan`` appear in *tools*.
    """
    r = dict(reasoning or {})
    tool_names = set(tools or [])

    think_enabled = r.get("think_enabled")
    if think_enabled is None:
        think_enabled = "think" in tool_names
    plan_enabled = r.get("plan_enabled")
    if plan_enabled is None:
        plan_enabled = "plan" in tool_names

    return {
        "think_enabled": bool(think_enabled),
        "think_mode": r.get("think_mode") or DEFAULT_THINK_MODE,
        "plan_enabled": bool(plan_enabled),
        "plan_format": r.get("plan_format") or DEFAULT_PLAN_FORMAT,
    }


def build_reasoning_tools(resolved: Dict[str, Any]) -> List[Any]:
    """Return the mode-aware think/plan tool instances for a resolved config."""
    from tools.think import make_think
    from tools.plan import make_plan

    tools: List[Any] = []
    if resolved.get("think_enabled"):
        tools.append(make_think(resolved.get("think_mode", DEFAULT_THINK_MODE)))
    if resolved.get("plan_enabled"):
        tools.append(make_plan(resolved.get("plan_format", DEFAULT_PLAN_FORMAT)))
    return tools


def build_reasoning_prompt(resolved: Dict[str, Any]) -> str:
    """Return a system-prompt section telling the agent when/how to reason.

    Empty string when neither capability is enabled.
    """
    if not resolved.get("think_enabled") and not resolved.get("plan_enabled"):
        return ""

    lines: List[str] = ["# Reasoning"]

    think_enabled = resolved.get("think_enabled")
    plan_enabled = resolved.get("plan_enabled")

    # Think-enabled agents always begin with an automatic think analysis of the
    # user's request (the runtime forces it before any other action). The prompt
    # explains this and tells the model what to do next.
    if think_enabled:
        first_step = (
            "Your first step is always a `think` analysis of the user's request — "
            "this is performed automatically before you act. Build on that "
            "analysis rather than repeating it."
        )
        if plan_enabled:
            first_step += " Next, call `plan` to lay out the concrete steps."
        lines.append(first_step)

    if plan_enabled:
        fmt = resolved.get("plan_format", DEFAULT_PLAN_FORMAT)
        fmt_clause = {
            "structured": "using section headers with sub-steps",
            "bullet": "as a flat bullet list of action items",
            "numbered": "as an ordered, numbered checklist",
            "freeform": "as a short narrative",
        }.get(fmt, "as a clear list of steps")
        lines.append(
            f"Before starting a multi-step task, call the `plan` tool to lay out "
            f"your approach {fmt_clause}. Revise the plan with `plan` if new "
            f"information changes the approach."
        )

    if think_enabled:
        mode = resolved.get("think_mode", DEFAULT_THINK_MODE)
        mode_clause = {
            "standard": (
                "As you work, continue to call `think` before and after key actions "
                "to plan the next step and analyse each result."
            ),
            "deep": (
                "As you work, reason thoroughly: call the `think` tool before every "
                "action and after every observation. Prefer over-thinking to "
                "under-thinking."
            ),
            "analytical": (
                "As you work, use the `think` tool to diagnose errors, verify your "
                "logic, and check assumptions before committing to an answer."
            ),
        }.get(mode, "As you work, call the `think` tool to reason step by step.")
        lines.append(mode_clause)

    return "\n\n".join(lines)


__all__ = [
    "resolve_reasoning",
    "build_reasoning_tools",
    "build_reasoning_prompt",
    "DEFAULT_THINK_MODE",
    "DEFAULT_PLAN_FORMAT",
]
