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
resumes with full context. In a task run ``ask_user`` parks the task in
awaiting_input until the person answers. A flow node never gets the gate: its
reply goes to the next node, a person is asked only by a human_interrupt node,
and ``build_flow_step_prompt`` takes the gate's place.
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


# An agent run as a flow node. Its reply is the next node's input, and the
# flow asks a person only through a human_interrupt node, so the gate above is
# off for it and this takes its place.
_FLOW_STEP_PROMPT = """## You are one step of a flow

Your reply is not read by a person: it is passed to the next step of the flow
as its input, and nobody can answer a question while this step runs.

- Do not ask questions and do not wait for answers. When something is missing
  or ambiguous, take the most reasonable reading, say in one line at the top
  which assumptions you made, and do the work.
- A short topic as input (for example "coffee trends 2026") is the whole brief:
  choose the scope yourself (subject, region, period, angle) and state it.
- When your tools and material cannot answer part of the step, say so plainly
  and return what you could do. Never invent sources, figures or links.
- Write plain text or Markdown only: no buttons, cards or other interactive
  blocks."""


def build_clarification_prompt(clarify_gate: bool) -> str:
    """Return the clarification-gate system-prompt snippet, or "" when disabled."""
    return _CLARIFY_PROMPT if clarify_gate else ""


def build_flow_step_prompt() -> str:
    """Return the system-prompt snippet for an agent run as a flow node."""
    return _FLOW_STEP_PROMPT


__all__ = ["build_clarification_prompt", "build_flow_step_prompt"]
