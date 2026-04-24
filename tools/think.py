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

    Call it to:
    - Plan what to do before reading/writing files
    - Analyse a tool result before deciding the next step
    - Examine an error message and figure out the root cause
    - Check your own logic before producing a final answer

    The thought is returned unchanged so it stays visible in your context.
    You can call think multiple times in a row to build up a reasoning chain.
    """
    return thought
