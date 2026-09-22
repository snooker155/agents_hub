"""
Shared agent-invocation core.

Every place that runs an agent — the task subprocess (``run_agent``), the flow
engine (``flow.dispatch``), the node worker (``runtime.node_run``) and the
in-container HTTP server (``runtime.http_server``) — repeats the same inner
idiom: attach a stats callback, time the call, run ``agent.run(prompt, ...)``,
turn the result (or an unhandled exception) into a ``process`` dict.

``invoke_agent`` captures exactly that invariant middle. Callers keep their own
*orchestration* (run records, task finalization, state writes, prompt building);
they delegate only the timed call + stats here.

It does **not** open/close run records or touch tasks — that is the caller's job.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from agents.callbacks import RunStatsCallback
from agents.callbacks.guards import ApprovalSignal
from agents.hooks import clear_pending_approval, pending_approval_for


@dataclass
class AgentInvocation:
    """Result of one timed agent run."""
    result: Any                 # the agent's AgentResult (ok/agent_output/error)
    duration_ms: int
    process: dict               # stats.build_process(duration_ms)
    stats: RunStatsCallback     # the stats callback (for prompt/completion tokens, etc.)


class _SyntheticFailed:
    """Stand-in AgentResult for an unhandled exception, so callers' result
    handling (ok/error/agent_output) works uniformly."""
    ok = False
    agent_output = None

    def __init__(self, error: str):
        self.error = error


class _AwaitingApproval:
    """Stand-in AgentResult for a run parked on a tool call awaiting approval.

    Shaped like the ``awaiting_input`` result the ask_user path produces (``ok``
    with a ``status`` the runner branches on), so the run record closes normally
    and only the task handling differs. It is not an ``AgentResult`` because that
    model has no field for the pending call, and adding one there would touch a
    model every surface serializes; every consumer of this reads it by attribute.
    """
    ok = True
    status = "awaiting_approval"
    error = None
    response = None
    pending_question = None

    def __init__(self, pending: Dict[str, Any]):
        self.pending_approval = dict(pending or {})
        self.steps: List[Any] = []
        self.changed_files: List[str] = []
        tool = str(self.pending_approval.get("tool") or "a tool")
        reason = str(self.pending_approval.get("reason") or "").strip()
        self.agent_output = (
            f"Waiting for your approval to call `{tool}`." + (f" {reason}" if reason else "")
        )


def invoke_agent(
    agent: Any,
    prompt: str,
    *,
    stats: Optional[RunStatsCallback] = None,
    extra_callbacks: Sequence[Any] = (),
    catch_exceptions: bool = True,
    run_id: Optional[str] = None,
    resume: Optional[Dict[str, Any]] = None,
    history: Optional[Sequence[Any]] = None,
) -> AgentInvocation:
    """Run ``agent.run(prompt, ...)`` timed, with a stats callback attached.

    - ``stats``: reuse an existing RunStatsCallback, or one is created.
    - ``extra_callbacks``: additional callbacks (stop guard, SSE publisher, ...).
    - ``catch_exceptions``: when True (default), an unhandled exception becomes a
      synthetic failed result so the caller's normal cleanup path runs; when
      False the exception propagates.
    - ``run_id``: forwarded to ``agent.run`` as a keyword when set. Both agent
      implementations take ``(self, instruction, **kwargs)``, so a positional
      second argument raises TypeError — which is what it used to do, breaking
      every caller that passed run_id (evals, replay, the lifecycle helper).

    - ``history``: the conversation before this turn, as LangChain messages. A
      chat surface passes it so prior turns reach the model as messages instead
      of as text folded into the prompt; every other caller (a task run, an
      eval, a delegation) has no conversation and passes nothing. Agents that do
      not understand it ignore the keyword.

    - ``resume``: ``{"run_id", "value", "key"}`` to continue a run the agent
      paused, instead of starting a new one. Only an agent that implements
      ``resume`` can do this — an imported one that declares ``resume_path``.
      Everything else is re-run with the answer folded into the prompt, which is
      how this hub has always resumed its own agents: they keep no state between
      runs, so replaying the conversation *is* the resume. An agent that
      suspended onto a checkpointer is the case that needs the other path.

    Returns an ``AgentInvocation`` with the result, duration, process dict, and
    the stats callback.
    """
    # A built agent is reused across runs (agent_cache), so a call an earlier run
    # was stopped on has to be forgotten before this one starts, or this run would
    # report itself parked on it before doing anything.
    clear_pending_approval(agent)

    stats_cb = stats if stats is not None else RunStatsCallback()
    callbacks: List[Any] = [stats_cb, *list(extra_callbacks)]

    # Track which agent is executing so delegation tools (run_agent_tool, etc.)
    # can enforce the caller's delegation allowlist. Token-based set/reset keeps
    # nested runs (an agent delegating to another) correctly scoped.
    from common.agent_context import current_agent_id
    _agent_token = current_agent_id.set(getattr(agent, "agent_id", None))

    t0 = time.perf_counter()
    try:
        resumable = resume and callable(getattr(agent, "resume", None))
        if resumable:
            result = agent.resume(
                str(resume.get("run_id") or ""),
                resume.get("value"),
                key=str(resume.get("key") or ""),
                callbacks=callbacks,
            )
        else:
            kwargs: Dict[str, Any] = {"callbacks": callbacks}
            if run_id is not None:
                kwargs["run_id"] = run_id
            if history:
                kwargs["history"] = list(history)
            result = agent.run(prompt, **kwargs)
    except ApprovalSignal as sig:
        # A tool call needs a human's yes. The agent implementation may let the
        # signal through (this branch) or, like StandardAgent, swallow it into a
        # failed result — which is why the parked call is also read back off the
        # agent's own gate below. Both paths end in the same result.
        result = _AwaitingApproval(sig.payload)
    except Exception as exc:  # noqa: BLE001
        if not catch_exceptions:
            raise
        import traceback
        result = _SyntheticFailed(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        current_agent_id.reset(_agent_token)

    # The run stopped on a gated tool call: report it as parked whatever the
    # executor made of the exception. The gate records the call on the agent's
    # own tool guard (never a module global), so concurrent runs in the backend
    # process cannot read each other's pending approval.
    if getattr(result, "status", "") != "awaiting_approval":
        pending = pending_approval_for(agent)
        if pending:
            result = _AwaitingApproval(pending)

    duration_ms = int((time.perf_counter() - t0) * 1000)
    return AgentInvocation(
        result=result,
        duration_ms=duration_ms,
        process=stats_cb.build_process(duration_ms),
        stats=stats_cb,
    )
