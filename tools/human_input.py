"""
Human-in-the-loop tool.

``ask_user`` lets an agent pause and ask the user for missing information instead
of guessing. It does not block the executor (that cannot suspend across a
request); instead it returns a JSON payload tagged with ``ASK_USER_SENTINEL``,
which :class:`agents.callbacks.guards.AskUserGuard` detects and turns into an
``AskUserSignal`` that ends the run cleanly.

What happens next depends on the surface the agent runs on:

- In a tracked task run (``runtime.agent_run``), the runner parks the task in the
  ``awaiting_input`` state, stores the question, and notifies the user. When the
  user answers, the task resumes by re-running the agent with the answer in
  context.
- In chat there is no task: the question simply becomes the agent's reply, and
  the next turn (which replays history) lets the agent continue once answered.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agents.callbacks.guards import ASK_USER_SENTINEL


class AskUserInput(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        description="The single, concise question to ask the user. Ask only for "
        "information you genuinely need and cannot infer.",
    )
    choices: Optional[List[str]] = Field(
        default=None,
        description="Optional list of discrete answer options. Provide them when "
        "the answer is one of a small, known set so the user can pick with one tap.",
    )


@tool("ask_user", args_schema=AskUserInput)
def ask_user(question: str, choices: Optional[List[str]] = None) -> str:
    """Pause and ask the user a clarifying question, then wait for their answer.

    Use this when you lack information you genuinely need to do the task well
    (scope, goals, constraints, a decision only the user can make). Calling it
    ENDS the current run: the question is shown to the user, and the work resumes
    with their answer once they reply. Ask one focused question at a time, and
    only when the answer actually changes what you do — never for details you can
    reasonably infer or that don't affect the outcome.
    """
    payload = {
        ASK_USER_SENTINEL: True,
        "question": question,
        "choices": list(choices or []),
    }
    return json.dumps(payload, ensure_ascii=False)


__all__ = ["ask_user"]
