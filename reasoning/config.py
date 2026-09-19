"""Reasoning (think / plan) capability config and prompt assembly.

The per-agent ``reasoning`` dict on an :class:`AgentSpec` is the source of
truth for *whether* the think/plan scratchpad tools are available and *how*
the agent should use them. Shape::

    {
        "think_enabled":  bool,  # add the think tool
        "think_mode":     str,   # standard | deep | analytical (think-tool prompt)
        "thinking_level": str,   # off | low | medium | high (native model reasoning)
        "plan_enabled":   bool,  # add the plan tool
        "plan_format":    str,   # structured | bullet | numbered | freeform
    }

``think_enabled`` toggles the ``think`` scratchpad *tool*; ``thinking_level`` is
a separate **model parameter** that turns on the provider's native reasoning
(Anthropic thinking budget, OpenAI/LM Studio ``reasoning_effort``, Ollama think
mode). The two are independent — an agent can use native model thinking without
the scratchpad tool, or vice-versa.

Older configs only carried ``think_mode`` / ``plan_format`` and relied on the
tools list to enable the tools, with native reasoning riding on ``think_mode``
gated by ``think_enabled``. :func:`resolve_reasoning` normalises every shape,
inferring the ``*_enabled`` flags from a legacy tools list and deriving
``thinking_level`` from the legacy ``think_mode`` when it is absent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


DEFAULT_THINK_MODE = "standard"
DEFAULT_PLAN_FORMAT = "structured"
DEFAULT_THINKING_LEVEL = "off"

# Valid native-reasoning levels (a model parameter, decoupled from the think tool).
THINKING_LEVELS = ("off", "low", "medium", "high")


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

    think_mode = r.get("think_mode") or DEFAULT_THINK_MODE

    # Native model reasoning level. Decoupled from the think tool, but for legacy
    # configs (no explicit ``thinking_level``) reproduce the old behavior where
    # native reasoning rode on ``think_mode`` and was gated by ``think_enabled``.
    thinking_level = r.get("thinking_level")
    if thinking_level is None:
        if think_enabled:
            thinking_level = "high" if think_mode == "deep" else "medium"
        else:
            thinking_level = DEFAULT_THINKING_LEVEL
    thinking_level = str(thinking_level).lower()
    if thinking_level not in THINKING_LEVELS:
        thinking_level = DEFAULT_THINKING_LEVEL

    return {
        "think_enabled": bool(think_enabled),
        "think_mode": think_mode,
        "thinking_level": thinking_level,
        "plan_enabled": bool(plan_enabled),
        "plan_format": r.get("plan_format") or DEFAULT_PLAN_FORMAT,
    }


def build_reasoning_tools(resolved: Dict[str, Any]):
    """Return ``(reasoning_tools, think_gate, plan_gate)`` for a resolved config.

    ``reasoning_tools`` are the mode-aware think/plan tool instances.

    ``think_gate`` is always ``None``: step-by-step enforcement is disabled, so
    agents are never forced to call ``think``. The slot is kept in the return
    shape so the dormant gate (reasoning/think_gate.py) can be re-enabled here
    without touching callers.

    ``plan_gate`` is a :class:`~reasoning.plan_gate.PlanGate` (or ``None`` when
    plan is off) that gates the ``plan`` / ``save_plan`` tools on a prior
    ``assess_complexity`` call, so the agent only plans genuinely complex
    requests and skips planning trivial ones.

    A plan-enabled agent also gets the persistent plan-store tools so it can
    save plans and track step status in the workspace ``.plans`` folder across
    runs — not just the ephemeral in-context ``plan`` scratchpad.
    """
    from reasoning.think import make_think
    from reasoning.plan import make_plan
    from reasoning.plan_store import (
        save_plan,
        get_plan,
        list_plans,
        update_plan_status,
        delete_plan,
    )
    from reasoning.plan_gate import PlanGate, make_assess_complexity

    tools: List[Any] = []
    # Think-gate enforcement is disabled: the `think` tool stays available as a
    # scratchpad (and the mode hints still encourage it), but the agent is never
    # FORCED to call it — no action refusals, no forced finish reviews. Models
    # with native reasoning think on their own; others follow the prompt hints.
    # (reasoning/think_gate.py is kept dormant for re-enabling.)
    think_gate = None
    plan_gate = None
    if resolved.get("think_enabled"):
        mode = resolved.get("think_mode", DEFAULT_THINK_MODE)
        tools.append(make_think(mode, on_think=None))
    if resolved.get("plan_enabled"):
        plan_gate = PlanGate()
        tools.append(make_assess_complexity(plan_gate))
        tools.append(make_plan(resolved.get("plan_format", DEFAULT_PLAN_FORMAT)))
        tools.extend([save_plan, get_plan, list_plans, update_plan_status, delete_plan])
    return tools, think_gate, plan_gate


def build_reasoning_prompt(resolved: Dict[str, Any]) -> str:
    """Return a system-prompt section telling the agent when/how to reason.

    Empty string when neither capability is enabled.
    """
    if not resolved.get("think_enabled") and not resolved.get("plan_enabled"):
        return ""

    lines: List[str] = ["# Reasoning"]

    think_enabled = resolved.get("think_enabled")
    plan_enabled = resolved.get("plan_enabled")

    # The think/plan tools are a *scratchpad*, not the deliverable. They help you
    # prepare, but the user's request is only satisfied once you have actually
    # carried out the work and produced a final answer. Make this explicit so the
    # agent does not stop after reasoning.
    if think_enabled:
        first_step = (
            "Start by calling `think` to analyse the user's request."
        )
        if plan_enabled:
            first_step += (
                " Then call `assess_complexity` to decide whether the request is "
                "complex enough to need an explicit plan; only call `plan` if it is."
            )
        first_step += (
            " These tools only record your reasoning — they do **not** complete "
            "the task. After reasoning, you must carry out the actual work "
            "(using your other tools as needed) and give the user a final answer. "
            "Never end your turn having only called `think`/`plan`."
        )
        lines.append(first_step)
    elif plan_enabled:
        lines.append(
            "`plan` only records your intended approach — it does **not** complete "
            "the task. After planning, carry out the actual work and give the user "
            "a final answer. Never end your turn having only called `plan`."
        )

    if plan_enabled:
        fmt = resolved.get("plan_format", DEFAULT_PLAN_FORMAT)
        fmt_clause = {
            "structured": "using section headers with sub-steps",
            "bullet": "as a flat bullet list of action items",
            "numbered": "as an ordered, numbered checklist",
            "freeform": "as a short narrative",
        }.get(fmt, "as a clear list of steps")
        lines.append(
            "First decide whether planning is warranted: call `assess_complexity` "
            "once up front. For a simple, direct, or single-step request, judge "
            "`needs_planning=False` and skip planning entirely — just do the work. "
            "The `plan` / `save_plan` tools stay locked until you have assessed."
        )
        lines.append(
            f"When the request **is** complex, call the `plan` tool to lay out "
            f"your approach {fmt_clause}. Revise the plan with `plan` if new "
            f"information changes the approach."
        )
        lines.append(
            "For substantial multi-step work that may span more than one run, "
            "persist the plan with `save_plan` (it is stored in the workspace's "
            "`.plans` folder and survives between sessions). As you execute, call "
            "`update_plan_status` to mark each step `in_progress` then `done`, and "
            "set the plan's overall status to `completed` when finished. Use "
            "`list_plans` / `get_plan` at the start of a run to resume an existing "
            "plan instead of starting over."
        )

    if think_enabled:
        mode = resolved.get("think_mode", DEFAULT_THINK_MODE)
        mode_clause = {
            "standard": (
                "As you work, continue to call `think` before and after key actions "
                "to plan the next step and analyse each result. Think as many times "
                "in a row as a step needs — the cadence is yours to decide."
            ),
            "deep": (
                "As you work, follow a reason-act loop: call `think` first to "
                "understand the request, then before every action to choose what to "
                "do and which tool to call, and after every observation to analyse "
                "the result and select the next step (call another tool, or move to "
                "answering). You decide how much to think at each point — call "
                "`think` several times in a row to reason through hard steps, weigh "
                "alternatives, or re-check earlier conclusions; one think per action "
                "is the minimum, not the limit. Before you finish, call `think` one "
                "last time to review your final answer — confirm it is correct and "
                "complete; if it is, present it, otherwise fix it and continue. "
                "Prefer over-thinking to under-thinking."
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
    "DEFAULT_THINKING_LEVEL",
    "THINKING_LEVELS",
]
