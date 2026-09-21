"""
The iteration loop: run the flow, judge the result, feed the judgement back.

::

    iteration:
      1. run    - the whole flow once, from its entry point, as its own flow run
      2. judge  - an agent scores the result against the loop's exit criterion
      3. carry  - the output and the judge's feedback seed the next iteration

Everything a flow already provides is reused rather than re-implemented: the
DAG walk is ``flow.engine.run_flow_engine`` and each pass is driven by the same
``flow.task_driver`` the subprocess flow runs use, so every node still opens a
real run record, writes a node log and publishes to the session. What a loop
adds is only what a DAG cannot express — the back edge and the condition on it.

One pass = one flow run record. That is deliberate: the flow editor's history
tab groups by ``flow_run_id``, so each iteration shows up as its own separately
inspectable execution instead of one smeared timeline, and the loop's iteration
row links straight to it.

Nothing here runs unbounded. Iteration cap, wall clock, cost ceiling, the
workspace budget, a no-improvement patience counter and an explicit stop are
all checked between iterations, and the caps in ``loops.models`` are hard.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from loops import store
from loops.evaluator import evaluate
from loops.models import (
    Iteration, Loop, LoopRun, MAX_ITERATIONS_CAP, MAX_WALL_SECONDS_CAP,
    Verdict, utc_iso,
)

log = logging.getLogger(__name__)


class LoopStopped(Exception):
    """The loop hit a limit or was asked to stop. ``reason`` is a STOP_REASONS key."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


# ── Streaming ────────────────────────────────────────────────────────────────

def _publish(loop_run_id: str, event: Dict[str, Any]) -> None:
    """Push an event to the ``loop:<id>`` channel. Never fatal."""
    try:
        from common.session_broker import broker
        broker.publish_threadsafe(f"loop:{loop_run_id}", event)
    except Exception:
        pass


# ── Per-iteration context ────────────────────────────────────────────────────

_RETRY_TEMPLATE = """{goal}

{sep}
THIS IS ATTEMPT {iteration}. THE PREVIOUS ATTEMPT WAS REVIEWED AND REJECTED.
{sep}

What the work must ultimately satisfy:
{criterion}

The reviewer scored the previous attempt {score} and said:
{reason}

What must change this time (this is the whole point of this attempt):
{feedback}

The previous attempt's result, to revise rather than restart:
{previous}

Do not begin from scratch unless the feedback says the approach itself is
wrong. Improve what is there, and address every point above."""

_SEP = "=" * 60


def build_iteration_context(
    *,
    goal: str,
    criterion: str,
    iteration: int,
    previous_output: str,
    verdict: Optional[Verdict],
) -> str:
    """The shared context handed to iteration *n*.

    The first pass sees the plain request. Every later pass sees the request,
    the rejected result and the reviewer's feedback — an agent that is not told
    *why* it is running again will produce the same thing again.
    """
    if iteration <= 1 or verdict is None:
        return goal
    return _RETRY_TEMPLATE.format(
        goal=goal, sep=_SEP, iteration=iteration,
        criterion=(criterion or "").strip() or "(the original request, fully satisfied)",
        score="(no score)" if verdict.score is None else f"{verdict.score:g}/100",
        reason=(verdict.reason or "(no reason given)").strip(),
        feedback=(verdict.feedback or "(no specific feedback — use your own judgement)").strip(),
        previous=(previous_output or "(the previous attempt produced no output)").strip(),
    )


def _seed_state(
    *, iteration: int, criterion: str, previous_output: str, verdict: Optional[Verdict],
    base_seed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Loop bookkeeping written into the flow's shared state, so a node can
    declare ``input: [iteration, evaluator_feedback]`` and read it directly."""
    seed: Dict[str, Any] = dict(base_seed or {})
    seed.update({
        "iteration": iteration,
        "exit_criterion": criterion,
        "previous_output": previous_output or "",
        "evaluator_feedback": (verdict.feedback if verdict else "") or "",
        "evaluator_reason": (verdict.reason if verdict else "") or "",
        "previous_score": verdict.score if verdict else None,
    })
    return seed


# ── Cost ─────────────────────────────────────────────────────────────────────

def _runs_cost(run_ids: List[str]) -> float:
    """Catalog-priced spend of a set of runs. Best effort — an unknown model is
    zero-cost, never an exception."""
    if not run_ids:
        return 0.0
    try:
        from common.pricing import load_price_map, run_cost_usd
        from managers.run_manager import get_run_by_id
        prices = load_price_map()
        total = 0.0
        for rid in run_ids:
            rec = get_run_by_id(rid)
            if rec:
                total += run_cost_usd(rec, prices)
        return round(total, 6)
    except Exception:
        return 0.0


# ── One iteration ────────────────────────────────────────────────────────────

def _run_flow_once(
    *,
    flow: Dict[str, Any],
    flow_id: str,
    flow_run_id: str,
    loop_run_id: str,
    iteration: int,
    workspace_path: str,
    workspace_name: Optional[str],
    task_id: str,
    session_id: str,
    title: str,
    shared_context: str,
    seed_state: Dict[str, Any],
    should_stop: Callable[[], bool],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Drive the flow once through the shared engine. Returns ``(finish_event,
    node_run_ids)``.

    The flow's own run record is opened and closed around the pass so the flow
    list, the editor's history tab and the crashed-run reconciler all treat an
    iteration exactly like any other flow execution.
    """
    from flow import run_store
    from flow.engine import run_flow_engine
    from flow.task_driver import build_task_driver

    run_store.open_flow_run(
        flow_run_id, flow_id,
        task_id=task_id, session_id=session_id, workspace=workspace_name,
        title=title, pid=os.getpid(), status="running",
    )

    args = argparse.Namespace(
        flow_id=flow_id, workspace=workspace_path, task_id=task_id,
        run_id=flow_run_id, session_id=session_id, desc=shared_context, seed="",
    )
    node_run_ids: Dict[str, str] = {}
    driver = build_task_driver(
        args=args, flow=flow, flow_id=flow_id, run_id=flow_run_id, task_id=task_id,
        session_id=session_id, task_title=title, node_run_ids=node_run_ids,
        port=int(os.environ.get("DASHBOARD_PORT", "8000")),
    )
    # The task driver stops on its own flow-run record; a loop must also stop
    # when the *loop* is stopped, so both are checked. FlowEngineDriver is a
    # plain dataclass, so replacing the hook is the intended extension point.
    _flow_should_stop = driver.should_stop
    driver.should_stop = lambda: should_stop() or _flow_should_stop()

    async def _drive() -> Dict[str, Any]:
        final: Dict[str, Any] = {}
        # The engine's flow_finish carries no state snapshot — only node_done
        # events do — so the last one is kept for the evaluator and the
        # iteration record.
        last_state: Dict[str, Any] = {}
        async for ev in run_flow_engine(
            flow, flow_id=flow_id, shared_context=shared_context,
            driver=driver, seed_state=seed_state,
        ):
            if ev["type"] == "flow_finish":
                final = {**ev, "state": last_state}
            elif ev["type"] == "node_done":
                last_state = ev.get("state") or last_state
                # Node-level progress for the live page: the loop's own stream
                # would otherwise go silent for the whole length of a pass.
                _publish(loop_run_id, {
                    "type": "node_done", "iteration": iteration,
                    "node_id": ev.get("node_id"), "label": ev.get("label"),
                    "ok": ev.get("ok"), "duration_ms": ev.get("duration_ms"),
                })
        return final

    try:
        final = asyncio.run(_drive())
    except Exception as e:  # noqa: BLE001 — a preflight/engine failure ends the pass, not the process
        run_store.close_flow_run(flow_run_id, status="failed", exit_code=1, error=str(e))
        raise

    stopped = bool(final.get("stopped"))
    failed = bool(final.get("any_failure"))
    run_store.close_flow_run(
        flow_run_id,
        status="stopped" if stopped else ("failed" if failed else "completed"),
        exit_code=1 if (stopped or failed) else 0,
    )
    return final, node_run_ids


# ── The loop ─────────────────────────────────────────────────────────────────

def run_loop(
    loop_id: str,
    *,
    goal: str = "",
    workspace: Optional[str] = None,
    task_id: Optional[str] = None,
    seed: Optional[Dict[str, Any]] = None,
    on_iteration: Optional[Callable[[Iteration], None]] = None,
) -> LoopRun:
    """Run a loop to convergence, or to whichever ceiling it hits first.

    Synchronous and long-running (iterations of a whole flow), so callers start
    it on a background thread and follow the ``loop:<loop_run_id>`` channel.
    """
    loop = store.get_loop(loop_id)
    if not loop:
        raise ValueError(f"Loop not found: {loop_id}")

    from flow import store as flow_store
    flow = flow_store.get_flow(loop.flow_id)
    if not flow:
        raise ValueError(f"Loop '{loop.name or loop_id}' references a missing flow: {loop.flow_id}")

    goal = (goal or "").strip() or (loop.description or "").strip() or (flow.get("description") or "")
    ws_name, ws_path, task_id, session_id = _prepare_context(loop, workspace, task_id, goal)

    # Publish the workspace on the context var the agent tools read. A
    # subprocess flow run sets AGENT_WORKSPACE instead, but this runs inside the
    # backend process, where a process-wide env var would race with every
    # concurrent chat request. asyncio.to_thread copies the context, so the value
    # set here reaches the node executions.
    from common.workspace_context import _workspace_ctx
    _workspace_ctx.set(ws_name)

    run = LoopRun(
        loop_id=loop_id, workspace=ws_name, goal=goal,
        task_id=task_id, session_id=session_id,
    )
    store.save_run(run)
    _publish(run.loop_run_id, {"type": "loop_start", **run.to_dict()})

    max_iterations = max(1, min(int(loop.max_iterations or 1), MAX_ITERATIONS_CAP))
    wall_cap = min(float(loop.max_wall_seconds or MAX_WALL_SECONDS_CAP), MAX_WALL_SECONDS_CAP)
    started = time.monotonic()

    history: List[Dict[str, Any]] = []
    verdict: Optional[Verdict] = None
    previous_output = ""
    best_score: Optional[float] = None
    stale = 0                     # consecutive iterations that failed to improve
    spend = 0.0

    try:
        from flow.launcher import _set_flow_running
        _set_flow_running(loop.flow_id, True)
    except Exception:
        pass

    try:
        for iteration in range(1, max_iterations + 1):
            _check_between_iterations(run.loop_run_id, started, wall_cap, spend, loop, ws_name)

            it = _execute_iteration(
                loop=loop, flow=flow, run=run, iteration=iteration,
                max_iterations=max_iterations, goal=goal,
                previous_output=previous_output, verdict=verdict,
                history=history, seed=seed, ws_name=ws_name, ws_path=ws_path,
                task_id=task_id, session_id=session_id,
            )
            spend = round(spend + it.cost, 6)
            previous_output = it.output or previous_output
            verdict = Verdict(
                score=it.score, verdict=it.verdict or "continue", reason=it.reason,
                feedback=it.feedback, raw=it.evaluator_raw, agent=it.evaluator_agent,
            )
            history.append({"iteration": iteration, "score": it.score, "reason": it.reason})

            run.iterations_done = iteration
            run.final_score = it.score
            run.total_cost = spend
            run.result = it.output
            if it.score is not None and (best_score is None or it.score > best_score):
                best_score, stale = it.score, 0
            elif it.score is not None:
                stale += 1
            run.best_score = best_score
            store.update_progress(
                run.loop_run_id, iterations_done=run.iterations_done,
                best_score=run.best_score, final_score=run.final_score,
                result=run.result, total_cost=run.total_cost,
            )

            _publish(run.loop_run_id, {"type": "iteration", **it.to_dict()})
            if on_iteration:
                try:
                    on_iteration(it)
                except Exception:
                    pass

            if it.status == "stopped":
                raise LoopStopped("stopped", "stop requested during the flow run")
            _check_convergence(loop, it, iteration, stale)

        raise LoopStopped("max_iterations", f"reached the cap of {max_iterations} iterations")

    except LoopStopped as e:
        # Hitting a ceiling is a completed run with a reason, not a failure: the
        # iterations it did produce are real work. Only a loop that could not
        # run its flow, or one the user stopped, is anything else.
        run.stop_reason = e.reason
        if e.reason == "stopped":
            run.status = "stopped"
        elif e.reason in ("flow_failed", "error"):
            run.status = "failed"
            run.error = e.detail
        else:
            run.status = "completed"
        log.info("loop %s finished: %s (%s)", run.loop_run_id, e.reason, e.detail)
    except Exception as e:  # noqa: BLE001
        log.exception("loop run failed")
        run.status = "failed"
        run.stop_reason = "error"
        run.error = f"{type(e).__name__}: {e}"

    run.total_cost = spend
    run.finished_at = utc_iso()
    store.save_run(run)
    try:
        from flow.launcher import _set_flow_running
        _set_flow_running(loop.flow_id, False)
    except Exception:
        pass
    _finalize_task(run)
    _publish(run.loop_run_id, {"type": "loop_done", **run.to_dict()})
    return run


def _execute_iteration(
    *,
    loop: Loop, flow: Dict[str, Any], run: LoopRun, iteration: int,
    max_iterations: int, goal: str, previous_output: str,
    verdict: Optional[Verdict], history: List[Dict[str, Any]],
    seed: Optional[Dict[str, Any]], ws_name: Optional[str], ws_path: str,
    task_id: str, session_id: str,
) -> Iteration:
    """Run one pass and judge it. Returns the persisted iteration record."""
    from uuid import uuid4

    started = time.monotonic()
    flow_run_id = str(uuid4())
    it = Iteration(
        loop_run_id=run.loop_run_id, iteration=iteration, flow_run_id=flow_run_id,
    )
    store.save_iteration(it)
    _publish(run.loop_run_id, {"type": "iteration_start", **it.to_dict()})

    context = build_iteration_context(
        goal=goal, criterion=loop.exit_criterion, iteration=iteration,
        previous_output=previous_output, verdict=verdict,
    )
    seed_state = _seed_state(
        iteration=iteration, criterion=loop.exit_criterion,
        previous_output=previous_output, verdict=verdict, base_seed=seed,
    )
    try:
        final, node_run_ids = _run_flow_once(
            flow=flow, flow_id=loop.flow_id, flow_run_id=flow_run_id,
            loop_run_id=run.loop_run_id, iteration=iteration,
            workspace_path=ws_path, workspace_name=ws_name, task_id=task_id,
            session_id=session_id,
            title=f"{loop.name or 'Loop'} — iteration {iteration}",
            shared_context=context, seed_state=seed_state,
            should_stop=lambda: store.stop_requested(run.loop_run_id),
        )
    except Exception as e:  # noqa: BLE001
        it.status = "failed"
        it.reason = f"the flow could not run: {type(e).__name__}: {e}"
        it.finished_at = utc_iso()
        it.duration_ms = int((time.monotonic() - started) * 1000)
        store.save_iteration(it)
        raise LoopStopped("flow_failed", it.reason)

    it.output = final.get("combined_output") or ""
    it.node_outputs = final.get("node_outputs") or {}
    it.state = final.get("state") or {}
    it.cost = _runs_cost(list(node_run_ids.values()))
    if final.get("stopped"):
        it.status = "stopped"
    elif final.get("any_failure"):
        it.status = "failed"
    else:
        it.status = "completed"

    # A stopped pass is not judged — there is nothing finished to have an
    # opinion about, and the evaluation would be another paid call after the
    # user has already asked for the loop to end.
    if it.status != "stopped":
        v, eval_cost = evaluate(
            loop, flow, goal=goal, output=it.output, iteration=iteration,
            history=history, state=final.get("state"), workspace=ws_path,
        )
        it.score, it.verdict = v.score, v.verdict
        it.reason, it.feedback = v.reason, v.feedback
        it.evaluator_agent, it.evaluator_raw = v.agent, v.raw
        it.cost = round(it.cost + eval_cost, 6)
        if v.error:
            it.reason = (it.reason + f" [evaluator error: {v.error}]").strip()

    it.duration_ms = int((time.monotonic() - started) * 1000)
    it.finished_at = utc_iso()
    store.save_iteration(it)
    return it


def _check_convergence(loop: Loop, it: Iteration, iteration: int, stale: int) -> None:
    """Raise :class:`LoopStopped` when this iteration ends the loop.

    ``min_iterations`` overrules an early "stop": the most common failure of a
    self-reviewing model is approving its own first draft, and a loop that never
    performs a second pass is just a flow with extra steps.
    """
    min_iterations = max(1, int(loop.min_iterations or 1))
    if it.status == "failed" and not it.output:
        raise LoopStopped("flow_failed", "the flow produced no output")

    if iteration < min_iterations:
        return

    target = loop.target_score
    if target is not None and it.score is not None and it.score >= float(target):
        raise LoopStopped("target_score", f"score {it.score:g} reached the target {float(target):g}")
    if it.verdict == "stop":
        raise LoopStopped("criterion_met", it.reason or "the evaluator accepted the result")
    if loop.patience and stale >= int(loop.patience):
        raise LoopStopped(
            "no_improvement",
            f"{stale} iterations without beating the best score — stopping rather than spending",
        )


def _check_between_iterations(
    loop_run_id: str, started: float, wall_cap: float, spend: float,
    loop: Loop, workspace: Optional[str],
) -> None:
    """Every ceiling, checked where an iteration boundary makes it safe to stop."""
    if store.stop_requested(loop_run_id):
        raise LoopStopped("stopped", "stopped by request")
    elapsed = time.monotonic() - started
    if elapsed > wall_cap:
        raise LoopStopped("wall_clock", f"wall-clock cap reached ({elapsed:.0f}s of {wall_cap:.0f}s)")
    if loop.cost_ceiling and spend >= float(loop.cost_ceiling):
        raise LoopStopped(
            "cost_ceiling", f"cost ceiling reached (${spend:.4f} of ${float(loop.cost_ceiling):.2f})"
        )
    try:
        from common.budget import check_budget
        check_budget(workspace)
    except LoopStopped:
        raise
    except Exception as e:  # noqa: BLE001
        raise LoopStopped("cost_ceiling", f"budget: {e}")


# ── Task / session plumbing ──────────────────────────────────────────────────

def _prepare_context(
    loop: Loop, workspace: Optional[str], task_id: Optional[str], goal: str,
) -> Tuple[Optional[str], str, str, str]:
    """Resolve the workspace, ensure a task and open one session for the whole
    loop. Every iteration runs inside them, so the run history reads as one
    piece of work rather than N unrelated flow runs."""
    from tasks import service as _ts
    from common.session_service import get_or_create_task_session
    from workspace import create_workspace_folder, resolve_task_workspace, as_param_dict

    ws_name = workspace or loop.workspace
    ws_name = (create_workspace_folder(ws_name).name if ws_name
               else create_workspace_folder().name)

    if not task_id:
        task = _ts.create_task(
            title=f"Loop: {loop.name or loop.loop_id}",
            description=goal, workspace=ws_name,
        )
        task_id = str(task.id)
    else:
        task = _ts.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

    params = as_param_dict({"workspace": ws_name})
    _, ws_path = resolve_task_workspace(task, params)

    session_id = get_or_create_task_session(
        title=getattr(task, "title", None) or f"Loop: {loop.name}",
        workspace=ws_name, is_flow=True, task_id=str(task_id),
    )
    try:
        _ts.update_task(task_id, session_id=session_id)
    except Exception:
        pass
    # Record the loop as the task's assignee, the same way a flow run does, so a
    # task attached to a loop shows what is working on it instead of appearing
    # unassigned for the whole run.
    try:
        _ts.assign_agent(
            task.id, f"Loop: {loop.name or loop.loop_id}",
            {"loop_id": loop.loop_id, "flow_id": loop.flow_id, "workspace": ws_name},
        )
    except Exception:
        pass
    return ws_name, str(ws_path), str(task_id), session_id


def _finalize_task(run: LoopRun) -> None:
    """Record the converged result on the task and close it out."""
    if not run.task_id:
        return
    try:
        from tasks.context import persist_task_result
        if run.result:
            persist_task_result(
                run.task_id, run.loop_run_id, run.result, agent_id="loop",
            )
    except Exception:
        pass
    try:
        from managers.run_manager import finalize_flow_task
        ok = run.status == "completed"
        finalize_flow_task(run.task_id, "completed" if ok else "failed",
                           0 if ok else 1, error=run.error)
    except Exception:
        pass


def estimate_cost(loop: Loop) -> Dict[str, Any]:
    """What a full run could cost before a single call is made.

    Deliberately reported as a *ceiling*: a loop that converges on the second
    pass costs a fraction of this, but the number that matters when deciding
    whether to press Run is the one where it never converges.
    """
    from flow import store as flow_store

    try:
        flow = flow_store.get_flow(loop.flow_id) or {}
    except Exception:
        flow = {}
    agent_nodes = [
        n for n in flow.get("nodes", []) or []
        if (n.get("data") or {}).get("agent_id") or n.get("agent_id")
    ]
    iterations = max(1, min(int(loop.max_iterations or 1), MAX_ITERATIONS_CAP))
    per_iteration = len(agent_nodes) + 1  # + the evaluation call
    return {
        "flow_id": loop.flow_id,
        "agent_nodes": len(agent_nodes),
        "max_iterations": iterations,
        "llm_calls_upper_bound": per_iteration * iterations,
        # `note` stays English for API consumers; `note_key` lets the UI
        # render the same sentence in the user's language.
        "note_key": "estimateNote",
        "note": (
            "Upper bound: every iteration runs the whole flow plus one "
            "evaluation call. A loop that converges early costs less; one that "
            "never converges costs exactly this. Set a cost ceiling."
        ),
    }


__all__ = ["run_loop", "estimate_cost", "build_iteration_context", "LoopStopped"]
