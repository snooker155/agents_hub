"""
Plan tool — gives the agent a structured scratchpad for upfront planning.

Where the think tool is for step-by-step reasoning *between* actions, the
plan tool is for producing a full, structured execution plan *before* starting
work.  The agent writes out the plan once (or revises it), then executes the
steps in order.

Based on the structured planning technique discussed in:
  "Thinking Clearly: Improving Reasoning with a Think Tool" (Anthropic, 2025)
"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class PlanInput(BaseModel):
    plan: str = Field(
        ...,
        description=(
            "A structured execution plan. "
            "List the high-level steps you will take, in order. "
            "Be explicit about any assumptions, dependencies between steps, "
            "and potential risks. "
            "The plan is returned unchanged so you can reference it later."
        ),
    )


@tool("plan", args_schema=PlanInput)
def plan(plan: str) -> str:
    """Create a structured plan before starting a multi-step task.

    Call it to:
    - Outline the steps needed to complete the task before executing any of them
    - Identify dependencies, risks, or open questions upfront
    - Revise the plan when new information changes the approach
    - Produce a checklist you can tick off as you execute each step

    The plan is returned unchanged so it stays visible in your context.
    Pair with the think tool for per-step reasoning as you execute the plan.
    """
    return plan


# Per-format guidance appended to the tool description so the configured
# planning format shapes *how* the agent writes its plan.
_PLAN_FORMAT_HINTS = {
    "structured": (
        "Format the plan with section headers and sub-steps under each section."
    ),
    "bullet": "Format the plan as a flat bullet list of action items.",
    "numbered": (
        "Format the plan as an ordered, numbered checklist of steps you can "
        "tick off as you execute each one."
    ),
    "freeform": "Write the plan as an unstructured narrative paragraph.",
}


def make_plan(fmt: str = "structured"):
    """Return a ``plan`` tool whose description is tailored to *fmt*."""
    hint = _PLAN_FORMAT_HINTS.get(fmt, _PLAN_FORMAT_HINTS["structured"])
    description = (plan.description or "").strip() + "\n\n" + hint

    @tool("plan", args_schema=PlanInput, description=description)
    def _plan(plan: str) -> str:
        return plan

    return _plan
