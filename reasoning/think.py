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
            "Your step-by-step reasoning or analysis. "
            "Write as much detail as needed. This will be returned "
            "as-is so you can read it on the next step."
        ),
    )


@tool("think", args_schema=ThinkInput)
def think(thought: str) -> str:
    """Use this tool to reason step by step before or after any action.

    Walk through these reasoning steps as you work, calling `think` to record
    each one:
    1. Understand the request — restate what is being asked and what a good
       result looks like.
    2. Choose what to do — decide the next concrete sub-goal.
    3. Decide which tool to call — pick the tool and the arguments for it (or
       conclude no tool is needed).
    4. Analyse the tool response — read the observation and judge whether it
       got you closer, including any errors.
    5. Select the next step — either loop back and call another tool, or
       conclude you have enough to answer.
    6. Create the response — assemble the final answer for the user.

    `think` is a scratchpad, not the deliverable: after reasoning you must go on
    to do the actual work and produce a final answer — never finish your turn
    having only called `think`.

    The thought is returned unchanged so it stays visible in your context. You
    decide how many times to think: call `think` as many times in a row as the
    problem needs — to work through one of the steps above across several
    thoughts, to explore alternatives, or to double-check earlier reasoning —
    not just once per action. Keep thinking until you are ready to act, then act.
    """
    return thought


# Per-mode guidance appended to the tool description so the configured
# reasoning depth nudges *how* the agent uses the scratchpad.
_MANDATORY_FIRST = (
    "Reason in explicit steps and record each with `think`: "
    "(1) understand the request, (2) choose what to do, (3) decide which tool "
    "to call, (4) analyse the tool response, (5) select the next step — call "
    "another tool or move to answering, (6) create the response. "
    "Always start by calling `think` to understand the request before your "
    "first action, and think again before each subsequent action — then "
    "proceed to do the actual work. You decide how many times to think: these "
    "are minimums, not a limit — call `think` several times in a row whenever a "
    "step needs more reasoning, and stop thinking once you are ready to act. "
    "`think` records reasoning only; it never completes the task, so do not end "
    "your turn after only thinking."
)

_THINK_MODE_HINTS = {
    "standard": (
        _MANDATORY_FIRST + " "
        "Move through the steps for each key action: pick the next sub-goal and "
        "tool, then evaluate the result before continuing."
    ),
    "deep": (
        _MANDATORY_FIRST + " "
        "Reason extensively at every step. Before each action lay out your "
        "understanding, your chosen sub-goal and the tool you will call; after "
        "each observation analyse it in detail before selecting the next step. "
        "When you have no more tools to call, do not answer yet: call `think` "
        "one final time to review your draft answer for correctness and "
        "completeness, and only then present it as the final response. "
        "Prefer thinking too much over too little."
    ),
    "analytical": (
        _MANDATORY_FIRST + " "
        "Put extra weight on steps 4 and 5: examine errors and unexpected "
        "results, verify your own logic, and check assumptions when analysing a "
        "tool response and selecting the next step."
    ),
}


def make_think(mode: str = "standard", on_think=None):
    """Return a ``think`` tool whose description is tailored to *mode*.

    The behavior is identical (the thought is echoed back); only the
    description differs so the model is nudged toward the configured depth.

    ``on_think`` is an optional zero-arg callback invoked each time the tool is
    called. It lets the step-by-step gate (see :mod:`reasoning.think_gate`) record
    that the agent reasoned, clearing the requirement before the next action.
    """
    hint = _THINK_MODE_HINTS.get(mode, _THINK_MODE_HINTS["standard"])
    description = (think.description or "").strip() + "\n\n" + hint

    @tool("think", args_schema=ThinkInput, description=description)
    def _think(thought: str) -> str:
        if on_think is not None:
            try:
                on_think()
            except Exception:
                pass
        return thought

    return _think
