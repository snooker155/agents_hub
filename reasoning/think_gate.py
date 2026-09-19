"""Step-by-step think enforcement (tool-gate).

The ``think`` tool's mode hints *ask* the agent to reason before each action,
but in a tool-calling loop the model can ignore that and act (or answer)
without ever calling ``think``. A LangChain callback can only observe events
and abort the run — it cannot make the model emit a ``think`` call.

This module enforces the behaviour at the *tool* level instead. A per-run
:class:`ThinkGate` tracks whether a ``think`` call has happened since the last
real action. Action tools are wrapped with :class:`GatedTool`: when the gate is
"armed" (think required but not yet done), the wrapped tool refuses to run and
returns a corrective message. That message goes back to the model as a tool
observation, so the model calls ``think`` and then retries the action — a real
loop-level enforcement that needs no executor rewrite.

Only the ``deep`` reasoning mode enforces a think before *every* action.
``standard`` requires a think before the *first* action only (so the agent
analyses the request up front) and then trusts the mode hints. ``analytical``
and unset modes do not gate (analytical is about diagnosing errors, not
pacing every step).

Exception: tools in :data:`_THINK_AFTER_TOOLS` (e.g. ``run_agent_tool``, which
returns a delegated worker's output) re-arm the gate in *every* enforcing mode
— standard included — for both the next action and the finish. The agent must
``think`` about the worker's result to decide whether to present it or chain
another agent before doing anything else.

The gate is intentionally per-build (constructed in ``build_reasoning_tools``)
so it is scoped to a single agent instance rather than shared globally.
"""
from __future__ import annotations

from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel


# Modes that gate every action on a preceding `think`.
_ENFORCE_EVERY_STEP = {"deep"}
# Modes that gate only the first action.
_ENFORCE_FIRST_ONLY = {"standard"}

# Tools whose RESULT must be analysed with `think` before the agent does
# anything else (act or finish), in every enforcing mode — standard included.
# run_agent_tool returns a delegated worker's output: the orchestrator must
# reason about that output to decide whether to present it or chain another
# agent, not just relay it.
_THINK_AFTER_TOOLS = {"run_agent_tool"}

# Reasoning tools never gate each other or themselves.
_REASONING_TOOL_NAMES = {
    "think",
    "plan",
    "save_plan",
    "get_plan",
    "list_plans",
    "update_plan_status",
    "delete_plan",
    "assess_complexity",
}


class ThinkGate:
    """Per-run state tracking whether the agent has thought since its last action.

    Two enforcement points exist:

    * **Before an action** — every gated tool refuses to run until a ``think``
      has happened since the last action (see :meth:`requires_think_before`).
    * **Before finishing** — in ``deep`` mode the agent must also call ``think``
      one last time to *review* its final answer before the turn ends (see
      :meth:`requires_review_before_finish`). The reviewing executor turns an
      un-reviewed finish back into a forced ``think`` instead of returning.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode or "standard"
        # Has the agent called `think` since the last gated action ran?
        self._thought_since_action = False
        # Has any gated action run yet this build?
        self._acted = False
        # Has a `think` happened since the most recent attempt to finish?
        # Re-armed every time the agent does real work (a non-think action) so
        # the closing review always reflects the latest answer.
        self._reviewed_since_work = False
        # Did the last action return a result that must be analysed with
        # `think` before acting or finishing (see _THINK_AFTER_TOOLS)? This
        # gates even in standard mode, where later actions are normally free.
        self._pending_result_review = False

    @property
    def enforces(self) -> bool:
        return self.mode in _ENFORCE_EVERY_STEP or self.mode in _ENFORCE_FIRST_ONLY

    @property
    def enforces_finish_review(self) -> bool:
        """True if the agent must `think` to review its answer before finishing.

        Only ``deep`` mode gates the finish; lighter modes trust the prompt.
        """
        return self.mode in _ENFORCE_EVERY_STEP

    def note_think(self) -> None:
        """Record that the agent reasoned; clears the requirement for the next action.

        A ``think`` also counts as reviewing the pending answer (and any pending
        worker result), so it satisfies the finish gate until the next piece of
        real work happens.
        """
        self._thought_since_action = True
        self._reviewed_since_work = True
        self._pending_result_review = False

    @property
    def pending_result_review(self) -> bool:
        """True while the last action's result awaits a `think` analysis."""
        return self._pending_result_review

    def requires_think_before(self, tool_name: str) -> bool:
        """True if *tool_name* must be preceded by a think and the agent hasn't thought."""
        if not self.enforces or tool_name in _REASONING_TOOL_NAMES:
            return False
        if self._thought_since_action:
            return False
        # A think-after tool just returned: its result must be analysed before
        # the next action, even in standard mode.
        if self._pending_result_review:
            return True
        # standard mode only gates the very first action.
        if self.mode in _ENFORCE_FIRST_ONLY and self._acted:
            return False
        return True

    def requires_review_before_finish(self) -> bool:
        """True if the agent is trying to finish without a fresh review ``think``."""
        # A pending worker result gates the finish in every enforcing mode.
        if self._pending_result_review:
            return True
        if not self.enforces_finish_review:
            return False
        return not self._reviewed_since_work

    def note_action(self, tool_name: str = "") -> None:
        """Record that a gated action ran; re-arms both the per-step and finish gates."""
        self._acted = True
        self._thought_since_action = False
        # New work was done, so any earlier review is stale — require a fresh one.
        self._reviewed_since_work = False
        self._pending_result_review = tool_name in _THINK_AFTER_TOOLS


# Worded defensively: some gated tools (e.g. run_agent_tool) instruct the model
# to present "the tool's output" to the user and stop — without the BLOCKED
# framing the model reports this refusal text as if it were that output instead
# of retrying.
_REFUSAL = (
    "BLOCKED — `{tool}` did NOT run. This is a reasoning-gate message, not the "
    "tool's output: do not report it to the user and do not finish your turn. "
    "Step-by-step reasoning is enabled for this agent, so first call the "
    "`think` tool to analyse this step, then call `{tool}` again with the same "
    "arguments to actually run it."
)

# Observation fed back when the agent tries to finish without a closing review.
# It is delivered as a synthetic tool result so the executor loops once more;
# the model then calls `think` to check its answer before finishing for real.
#
# The drafted answer is embedded so the model reviews *that specific text* rather
# than re-deriving an answer from scratch (which produced a near-duplicate, and
# the discarded draft's tokens have already been streamed to the user). After the
# review the model presents the answer once — unchanged if it holds up, corrected
# if the review found a problem.
_REVIEW_REQUIRED = (
    "Before finishing, call the `think` tool one more time to review the answer "
    "you just drafted (shown below): check it actually addresses the request, is "
    "correct, and is complete. If the review surfaces a problem, fix it; "
    "otherwise keep it as-is. Then present that answer as your final response — "
    "do not re-derive it from scratch.\n\n"
    "--- Drafted answer under review ---\n"
    "{draft}\n"
    "--- end drafted answer ---\n"
    "(Step-by-step reasoning is enabled for this agent.)"
)

# Variant used when the agent tries to finish while a delegated worker's result
# is still unreviewed: the think must judge the worker output and decide
# between presenting it and chaining another agent.
_RESULT_REVIEW_REQUIRED = (
    "Before finishing, call the `think` tool to analyse the worker output you "
    "just received: judge whether it actually fulfils the user's request, and "
    "decide whether to present it as-is or to continue by delegating follow-up "
    "work to another agent. Your drafted answer is shown below — after the "
    "review either present it (corrected if needed) or take the next action.\n\n"
    "--- Drafted answer under review ---\n"
    "{draft}\n"
    "--- end drafted answer ---\n"
    "(Step-by-step reasoning is enabled for this agent.)"
)


class GatedTool(BaseTool):
    """Wraps a tool so it refuses to run until the agent has called ``think``."""

    # pydantic model fields
    inner: BaseTool
    gate: ThinkGate

    # Mirror the wrapped tool's public surface so the LLM sees no difference.
    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    def __init__(self, inner: BaseTool, gate: ThinkGate, **kwargs: Any) -> None:
        super().__init__(
            inner=inner,
            gate=gate,
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        if self.gate.requires_think_before(self.name):
            return _REFUSAL.format(tool=self.name)
        self.gate.note_action(self.name)
        # Strip the run manager LangChain injects; the inner tool manages its own.
        kwargs.pop("run_manager", None)
        return self.inner.run(_merge_tool_input(args, kwargs))

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        if self.gate.requires_think_before(self.name):
            return _REFUSAL.format(tool=self.name)
        self.gate.note_action(self.name)
        kwargs.pop("run_manager", None)
        return await self.inner.arun(_merge_tool_input(args, kwargs))


def _merge_tool_input(args: tuple, kwargs: dict) -> Any:
    """Reconstruct the tool input dict/value from the *_run* call shape."""
    if kwargs:
        return dict(kwargs)
    if len(args) == 1:
        return args[0]
    return list(args)


def make_reviewing_executor(executor, gate: ThinkGate):
    """Wrap an ``AgentExecutor`` so a finish is gated on a closing review ``think``.

    When ``gate.enforces_finish_review`` is true and the agent tries to finish
    without having called ``think`` since its last real work, the executor turns
    the finish back into a forced ``think``: a synthetic tool observation
    (:data:`_REVIEW_REQUIRED`) is yielded instead of the ``AgentFinish``, so the
    loop continues and the model reviews its answer before finishing for real.

    Standard mode is wrapped too: it normally finishes freely, but a pending
    worker-result review (see _THINK_AFTER_TOOLS) gates the finish in every
    enforcing mode. Returns *executor* unchanged for non-enforcing gates.
    """
    if gate is None or not gate.enforces:
        return executor

    from langchain.agents import AgentExecutor
    from langchain_core.agents import AgentAction, AgentFinish, AgentStep

    # A synthetic action so the forced review shows up as a normal step in the
    # scratchpad/intermediate_steps rather than a mysterious bare observation.
    _review_action = AgentAction(
        tool="think",
        tool_input={"thought": "(review required before finishing)"},
        log="",
    )

    # Guard against a model that keeps trying to finish without ever calling
    # `think`: after this many forced reviews in a row, let the finish through
    # rather than loop forever. Reset whenever a real review clears the gate.
    _MAX_FORCED_REVIEWS = 3
    forced = {"count": 0}

    def _draft_text(finish) -> str:
        """The answer text the model just drafted, from the AgentFinish."""
        try:
            values = getattr(finish, "return_values", None) or {}
            return str(values.get("output", "") or "")
        except Exception:
            return ""

    def _intercept(item):
        """Swap an un-reviewed AgentFinish for a forced-review AgentStep."""
        if isinstance(item, AgentFinish) and gate.requires_review_before_finish():
            if forced["count"] >= _MAX_FORCED_REVIEWS:
                return item  # give up forcing; return the answer as-is
            forced["count"] += 1
            # Show the model its own draft so it reviews that text instead of
            # re-deriving a near-duplicate from scratch. A pending worker result
            # gets the variant that asks it to judge that output and decide
            # whether to chain another agent.
            template = (
                _RESULT_REVIEW_REQUIRED if gate.pending_result_review else _REVIEW_REQUIRED
            )
            observation = template.format(draft=_draft_text(item))
            return AgentStep(action=_review_action, observation=observation)
        # A genuine review happened (gate cleared) — reset the safety counter.
        forced["count"] = 0
        return item

    class ReviewingAgentExecutor(AgentExecutor):
        def _iter_next_step(self, *args, **kwargs):
            for item in super()._iter_next_step(*args, **kwargs):
                yield _intercept(item)

        async def _aiter_next_step(self, *args, **kwargs):
            async for item in super()._aiter_next_step(*args, **kwargs):
                yield _intercept(item)

    # Re-class the already-built executor in place; all validated fields carry
    # over since ReviewingAgentExecutor only overrides two methods.
    executor.__class__ = ReviewingAgentExecutor
    return executor


def gate_tools(tools: list, gate: ThinkGate) -> list:
    """Wrap each non-reasoning tool in *tools* with the gate, when it enforces.

    Reasoning tools and any tool when the gate does not enforce are returned
    unchanged.
    """
    if not gate.enforces:
        return tools
    wrapped = []
    for t in tools:
        name = getattr(t, "name", "")
        if name in _REASONING_TOOL_NAMES or not isinstance(t, BaseTool):
            wrapped.append(t)
        else:
            wrapped.append(GatedTool(t, gate))
    return wrapped


__all__ = ["ThinkGate", "GatedTool", "gate_tools", "make_reviewing_executor"]
