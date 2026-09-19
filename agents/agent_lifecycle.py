"""
Shared agent *run* skeleton — the common build→invoke→finalize arc.

``agent_invoke.invoke_agent`` captures the invariant *middle* (timed call +
stats) but deliberately stays free of run records and task finalization. The two
full agent runners — the task subprocess (``runtime.agent_run``) and the flow
engine's in-process node executor (``runtime.flow_run._run_agent_node``) — share more
than that middle: both

  1. build the agent (``create_agent``), treating a build failure as a failed run,
  2. optionally post-process the freshly built agent (e.g. record its resolved
     provider/model, emit an init line),
  3. invoke it via ``invoke_agent``,
  4. finalize on success or failure.

They differ only in the *edges*: how the run record is opened, what prompt is
built, how completion is recorded (task lifecycle vs. node exec log), and what
the caller returns (sys.exit vs. DispatchResult). ``run_agent_lifecycle`` owns
the shared arc and takes the divergent edges as callables, so neither caller has
to re-derive the build/invoke/branch skeleton.

This module layers on top of ``invoke_agent``; it still does not itself know the
*shape* of any run store — the ``on_success`` / ``on_failure`` hooks do.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from agents.agent_factory import create_agent
from agents.agent_invoke import invoke_agent, AgentInvocation


def run_agent_lifecycle(
    agent_id: str,
    workspace: Optional[str],
    prompt: str,
    *,
    overrides: Optional[dict] = None,
    stats: Any = None,
    extra_callbacks: Sequence[Any] = (),
    catch_invoke_exceptions: bool = True,
    run_id: Optional[str] = None,
    after_build: Optional[Callable[[Any], None]] = None,
    on_build_error: Callable[[str], Any],
    on_success: Callable[[Any, AgentInvocation], Any],
    on_failure: Callable[[str, AgentInvocation], Any],
) -> Any:
    """Run the shared build→invoke→finalize arc, delegating the divergent edges.

    Steps:
      1. ``create_agent(agent_id, workspace, **overrides)``. If it raises, call
         ``on_build_error(error_msg)`` and return its result (no invoke happens).
      2. If given, call ``after_build(agent)`` (e.g. to record the agent's
         resolved provider/model or print an init line).
      3. ``invoke_agent(agent, prompt, stats=, extra_callbacks=, run_id=)``.
      4. Dispatch on ``invocation.result.ok`` to ``on_success(result, invocation)``
         or ``on_failure(error_msg, invocation)`` and return that hook's value.

    The hooks own all run-record / task / return-value specifics; this function
    owns only the ordering and the create_agent failure handling that both the
    subprocess and in-process runners previously hand-rolled.
    """
    try:
        agent = create_agent(agent_id, workspace=workspace, **(overrides or {}))
    except Exception as exc:  # noqa: BLE001 - a build failure is a failed run, not a crash
        return on_build_error(f"{type(exc).__name__}: {exc}")

    if after_build is not None:
        after_build(agent)

    invocation = invoke_agent(
        agent, prompt,
        stats=stats,
        extra_callbacks=extra_callbacks,
        catch_exceptions=catch_invoke_exceptions,
        run_id=run_id,
    )
    result = invocation.result
    if result.ok:
        return on_success(result, invocation)
    error_msg = str(getattr(result, "error", None) or "unknown error")
    return on_failure(error_msg, invocation)
