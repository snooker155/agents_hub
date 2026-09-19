"""
Chat clarification gate.

When an agent has ``clarify_gate`` enabled (AgentSpec.clarify_gate, toggled per
agent on the Agent details page), ``agent_factory`` injects the snippet built
here into the system prompt — the same mechanism used for reasoning guidance and
the structured response format.

The snippet tells the agent to check whether it has enough information before
producing deliverables and, if not, to ask concise clarifying questions and stop
instead of charging ahead on assumptions. In chat this works naturally: each
turn replays the conversation history, so when the user answers, the agent
resumes with full context. In a background task/flow run the agent's questions
simply become its output (it cannot truly pause yet — that is a separate,
awaiting-input task-state feature).
"""
from __future__ import annotations


_CLARIFY_PROMPT = """## Gather requirements before executing

Before you produce deliverables or take action, judge whether you actually have
enough information to do the task well.

- If the request is clear and you have what you need, proceed as normal.
- If key information is missing or ambiguous (scope, goals, constraints,
  audience, format, edge cases, …), do NOT guess or assume. Ask the user and
  wait for their answer before doing the work.

How to ask:
- If you have an `ask_user` tool, call it with your question (and `choices` when
  the answer is one of a small known set). It pauses the work until the user
  answers, then you resume with their answer — ask one focused question per call.
- Otherwise, ask up to 3 concise questions in plain text and STOP — end your
  reply there. When the answers are discrete and you can emit an interactive
  reply, offer them as buttons so the user can answer with one tap.

Ask only for what genuinely blocks good work — never interrogate the user about
details you can reasonably infer or that don't change the outcome."""


def build_clarification_prompt(clarify_gate: bool) -> str:
    """Return the clarification-gate system-prompt snippet, or "" when disabled."""
    return _CLARIFY_PROMPT if clarify_gate else ""


__all__ = ["build_clarification_prompt"]
