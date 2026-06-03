"""
Think tool — gives the agent a scratchpad to reason step by step.

In tool-calling agents there is no "Thought:" text between actions.
This tool makes reasoning explicit: the agent calls think(...) to write
out its analysis, and the thought appears in the scratchpad as a real
observation the model can read on the next turn.

Based on the technique described in:
  "Thinking Clearly: Improving Reasoning with a Think Tool" (Anthropic, 2025)
"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class ThinkInput(BaseModel):
    thought: str = Field(
        ...,
        description=(
            "Your step-by-step reasoning, analysis, or plan. "
            "Write as much detail as needed. This will be returned "
            "as-is so you can read it on the next step."
        ),
    )


@tool("think", args_schema=ThinkInput)
def think(thought: str) -> str:
    """Use this tool to reason step by step before or after any action.

    STRICT REQUIREMENT: You MUST call `think` FIRST, before calling any other
    tool. Always begin a task by calling `think` to plan your approach, and call
    `think` again before each subsequent action. Never invoke another tool until
    you have called `think` for the current step.

    Call it to:
    - Plan what to do before reading/writing files
    - Analyse a tool result before deciding the next step
    - Examine an error message and figure out the root cause
    - Check your own logic before producing a final answer

    The thought is returned unchanged so it stays visible in your context.
    You can call think multiple times in a row to build up a reasoning chain.
    """
    return thought


# Per-mode guidance appended to the tool description so the configured
# reasoning depth nudges *how* the agent uses the scratchpad.
_MANDATORY_FIRST = (
    "ALWAYS call `think` first, before any other tool, and again before each "
    "subsequent action. Do not use any other tool until you have called `think`."
)

_THINK_MODE_HINTS = {
    "standard": (
        _MANDATORY_FIRST + " "
        "Use this before and after key actions: plan the next step, then "
        "analyse the result before continuing."
    ),
    "deep": (
        _MANDATORY_FIRST + " "
        "Reason extensively. Call think before every action to lay out your "
        "approach, and after every observation to evaluate it. Prefer thinking "
        "too much over too little."
    ),
    "analytical": (
        _MANDATORY_FIRST + " "
        "Focus this tool on diagnosis: examine errors and unexpected results, "
        "verify your own logic, and check assumptions before producing an answer."
    ),
}


def make_think(mode: str = "standard"):
    """Return a ``think`` tool whose description is tailored to *mode*.

    The behavior is identical (the thought is echoed back); only the
    description differs so the model is nudged toward the configured depth.
    """
    hint = _THINK_MODE_HINTS.get(mode, _THINK_MODE_HINTS["standard"])
    description = (think.description or "").strip() + "\n\n" + hint

    @tool("think", args_schema=ThinkInput, description=description)
    def _think(thought: str) -> str:
        return thought

    return _think
