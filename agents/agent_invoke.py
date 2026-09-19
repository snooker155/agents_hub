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
from typing import Any, List, Optional, Sequence

from agents.callbacks import RunStatsCallback


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


def invoke_agent(
    agent: Any,
    prompt: str,
    *,
    stats: Optional[RunStatsCallback] = None,
    extra_callbacks: Sequence[Any] = (),
    catch_exceptions: bool = True,
    run_id: Optional[str] = None,
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

    Returns an ``AgentInvocation`` with the result, duration, process dict, and
    the stats callback.
    """
    stats_cb = stats if stats is not None else RunStatsCallback()
    callbacks: List[Any] = [stats_cb, *list(extra_callbacks)]

    # Track which agent is executing so delegation tools (run_agent_tool, etc.)
    # can enforce the caller's delegation allowlist. Token-based set/reset keeps
    # nested runs (an agent delegating to another) correctly scoped.
    from common.agent_context import current_agent_id
    _agent_token = current_agent_id.set(getattr(agent, "agent_id", None))

    t0 = time.perf_counter()
    try:
        if run_id is not None:
            result = agent.run(prompt, run_id=run_id, callbacks=callbacks)
        else:
            result = agent.run(prompt, callbacks=callbacks)
    except Exception as exc:  # noqa: BLE001
        if not catch_exceptions:
            raise
        import traceback
        result = _SyntheticFailed(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        current_agent_id.reset(_agent_token)

    duration_ms = int((time.perf_counter() - t0) * 1000)
    return AgentInvocation(
        result=result,
        duration_ms=duration_ms,
        process=stats_cb.build_process(duration_ms),
        stats=stats_cb,
    )
