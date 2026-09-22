"""
What a finished run does to the task that owns it.

A run ending is only half an event: the task it was executing has to advance,
block, retry, park for user input or hand off to a reviewer, and any session
continuation waiting on it has to fire. That state machine lives here, apart
from the run store and the run lifecycle, because it is the layer that knows
about tasks, agents and the orchestrator — and because it is the layer most
likely to change when the pipeline does.

Imports run one way only: this module calls the store and the lifecycle,
neither of them calls back into it.
"""
from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from common import db

from .lifecycle import get_run_by_id, run_log_path
from .store import _upsert_run, _utc_now_iso


def finalize_flow_task(task_id: str, status: str, exit_code: int, *, error: str | None = None) -> None:
    """Update a task's status after its flow run finishes.

    A flow is not an agent run — it has no meta run record — so this resolves
    the task directly by id and applies the same terminal-status rules a
    resolving agent would (advance to resolved / continuous-followup on success,
    block on failure), then fires any pending session continuations.
    """
    try:
        from tasks import service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id))

        if status == "completed":
            agent_set_statuses = {
                _ts.TaskStatus.reviewing,
                _ts.TaskStatus.reviewed,
                _ts.TaskStatus.blocked,
                _ts.TaskStatus.done,
            }
            current_task = _ts.get_task(tid)
            if current_task and current_task.status not in agent_set_statuses:
                ws_name = str(getattr(current_task, "workspace", "") or "default")
                try:
                    from workspace import get_workspace_metadata
                    followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                except Exception:
                    followup_mode = "single"
                if followup_mode == "continuous":
                    _ts.update_task(tid, status=_ts.TaskStatus.in_progress)
                else:
                    _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                _ts.clear_agent(tid)
        else:
            reason = (error or "").strip() or f"flow exited with code {exit_code}"
            _ts.block_task(tid, reason=reason)
            _ts.clear_agent(tid)
            try:
                _ts.append_task_activity_log(tid, "run_failed", f"Flow failed: {reason}")
            except Exception:
                pass

        # Trigger any session continuations waiting for this task
        try:
            from common.session_service import pop_continuations_for_task
            for cont in pop_continuations_for_task(str(task_id)):
                try:
                    _trigger_session_continuation(cont, status)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def park_task_awaiting_input(run_id: str, question: Dict[str, Any], agent_id: str = "") -> None:
    """Pause a task because its agent called ``ask_user``.

    Sets the owning task to ``awaiting_input`` with the pending question stored on
    it, keeps the agent assignment (so the resume path knows who to re-run), and
    notifies the user. Crucially it does NOT run the normal completion path
    (``finalize_task_from_run``): the task is paused, not resolved, so any session
    continuation waiting on this task stays pending until the user answers and the
    resumed run finishes. Best-effort — failures are swallowed.
    """
    try:
        run = get_run_by_id(run_id)
        task_id_str = (run or {}).get("task_id")
        if not task_id_str:
            return
        from tasks import service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id_str))

        q_text = str((question or {}).get("question") or "").strip()
        pending = {
            "question": q_text,
            "choices": list((question or {}).get("choices") or []),
            "agent_id": agent_id or str((run or {}).get("agent_id") or ""),
            "run_id": run_id,
            "asked_at": _utc_now_iso(),
        }
        # An imported agent that suspended keeps its own handle on the question
        # and knows which node it stopped in. Both are its to interpret, and the
        # resume call hands them straight back, so they travel with the question
        # rather than being dropped here.
        for extra in ("key", "node"):
            value = (question or {}).get(extra)
            if value:
                pending[extra] = str(value)
        _ts.update_task(tid, status=_ts.TaskStatus.awaiting_input, pending_question=pending)
        try:
            _ts.append_task_activity_log(tid, "awaiting_input", f"Agent asked: {q_text}", run_id=run_id, agent_id=pending["agent_id"])
        except Exception:
            pass

        # Surface the question to the user (inbox + dashboard bell).
        try:
            from plans import service as _plan_service
            current_task = _ts.get_task(tid)
            _plan_service.create_notification(
                title="A task needs your input",
                body=q_text or "The agent is waiting for your answer.",
                severity="info",
                source={"origin": "agent", "task_id": str(task_id_str)},
                workspace=str(getattr(current_task, "workspace", "") or "") or None,
                channels=["dashboard"],
            )
        except Exception:
            pass
    except Exception:
        pass


# How many fix->review cycles a task may go through before the pipeline stops
# looping and hands it back to the user (see _auto_start_review below). The
# orchestrator's prompt used to spell out this number itself; it is enforced
# here instead so the limit cannot drift from what the prompt says. Plain
# module constant rather than a common.config setting: no other per-task loop
# limit lives in settings yet, and this one is internal wiring, not something
# an operator tunes per deployment.
MAX_REVIEW_CYCLES = 3


def _auto_start_review(tid, task) -> bool:
    """Deterministically start a code_reviewer run on a freshly resolved task.

    Called when a continuation orchestrator run finishes with the task resolved:
    the orchestrator is the last LLM actor on the task, so if it did not chain
    the reviewer itself, nothing else would. Skipped (returns False) when the
    reviewer is not registered, not allowed in the workspace, or the workspace
    is not in subprocess mode (node mode dispatches through its polling loop).

    Also the choke point for the fix->review cycle limit: this function fires
    every time a continuation run resolves the task, including every fix cycle
    after a reviewer rejection, so counting review starts here catches the
    whole loop rather than just its first pass. Once ``review_cycles`` reaches
    ``MAX_REVIEW_CYCLES`` the task is blocked for the user instead of starting
    another review, and the user is notified the same way ``ask_user`` parks a
    task for them.
    """
    try:
        from tasks import service as _ts
        from agents.registry import get_agent
        from workspace import get_workspace_metadata

        if not get_agent("code_reviewer"):
            return False
        ws_name = str(getattr(task, "workspace", "") or "default")
        metadata = get_workspace_metadata(ws_name)
        allowed = metadata.get("allowed_agents")  # None means unrestricted
        if allowed is not None and "code_reviewer" not in allowed:
            return False
        if metadata.get("orchestrator", {}).get("execution_mode", "subprocess") == "node":
            return False

        cycles = int(getattr(task, "review_cycles", 0) or 0)
        if cycles >= MAX_REVIEW_CYCLES:
            reason = (
                f"Review cycle limit ({MAX_REVIEW_CYCLES}) reached for this task — "
                "it needs your input instead of another automatic fix/review pass."
            )
            _ts.block_task(tid, reason=reason)
            _ts.append_task_activity_log(tid, "review_cycle_limit", reason)
            try:
                from plans import service as _plan_service
                _plan_service.create_notification(
                    title="A task hit its review cycle limit",
                    body=reason,
                    severity="warning",
                    source={"origin": "agent", "task_id": str(tid)},
                    workspace=ws_name,
                    channels=["dashboard"],
                )
            except Exception:
                pass
            return False

        from agents import agent_launcher
        review_run_id, _sess = agent_launcher.start_run(str(tid), "code_reviewer", None)
        _ts.assign_agent(tid, "code_reviewer", None, run_id=review_run_id)
        _ts.update_task(tid, status=_ts.TaskStatus.reviewing, review_cycles=cycles + 1)
        _ts.append_task_activity_log(
            tid,
            "auto_review",
            "Task resolved — code_reviewer started automatically",
            run_id=review_run_id,
            agent_id="code_reviewer",
        )
        return True
    except Exception:
        return False


def _maybe_retry_failed_run(tid, run: Dict[str, Any], reason: str) -> bool:
    """Re-dispatch a failed worker run instead of blocking, when the workspace's
    orchestrator ``max_retries`` still allows it.

    Opt-in (default ``max_retries=0`` disables it) and deliberately conservative:
    only worker-agent runs are retried (never orchestrator/reviewer control runs
    or a run the user stopped), the failure reason is appended to the retry
    instruction, and the per-task ``retry_count`` is capped hard so a persistently
    failing task blocks after N attempts rather than looping forever. Returns True
    when a retry was dispatched (caller then skips blocking the task).
    """
    try:
        from tasks import service as _ts

        # Never retry a run the user stopped.
        run_status = str((run or {}).get("status") or "").lower()
        err_text = str((run or {}).get("error") or "").lower()
        if run_status == "stopped" or "stopped by user" in err_text:
            return False

        agent_id = str((run or {}).get("agent_id") or "")
        if not agent_id or agent_id in ("orchestrator", "code_reviewer"):
            return False

        task = _ts.get_task(tid)
        if not task:
            return False

        ws_name = str(getattr(task, "workspace", "") or "default")
        try:
            from workspace import get_workspace_metadata
            max_retries = int(get_workspace_metadata(ws_name).get("orchestrator", {}).get("max_retries", 0) or 0)
        except Exception:
            max_retries = 0
        if max_retries <= 0:
            return False

        attempts = int(getattr(task, "retry_count", 0) or 0)
        if attempts >= max_retries:
            return False

        # Feed the failure forward so the retry can react to what went wrong.
        params = dict(getattr(task, "assigned_agent_params", None) or {})
        prev_desc = str(params.get("description") or getattr(task, "description", "") or "").strip()
        params["description"] = (
            f"{prev_desc}\n\n[Retry {attempts + 1}/{max_retries}] The previous attempt failed: {reason}"
        ).strip()

        # Dispatch first — if launching raises (e.g. a budget cap), we fall through
        # to blocking without having mutated the task's retry bookkeeping.
        from agents import agent_launcher
        new_run_id, _sess = agent_launcher.start_run(str(tid), agent_id, params)
        _ts.assign_agent(tid, agent_id, params, run_id=new_run_id)
        _ts.update_task(tid, status=_ts.TaskStatus.in_progress, retry_count=attempts + 1)
        _ts.append_task_activity_log(
            tid,
            "run_retry",
            f"Retry {attempts + 1}/{max_retries} after failure: {reason}",
            run_id=new_run_id,
            agent_id=agent_id,
        )
        return True
    except Exception:
        return False


def finalize_task_from_run(run_id: str, status: str, exit_code: int) -> None:
    """Update the owning task's status after a run completes or fails."""
    try:
        run = get_run_by_id(run_id)
        task_id_str = (run or {}).get("task_id")
        if not task_id_str:
            return
        from tasks import service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id_str))

        # Final status / tokens are already persisted on the run record itself
        # (status, finished_at, process.token_usage); no separate sidecar update.

        # Record a 'task' episode for agents with shared memory (best-effort).
        try:
            agent_id_for_run = str((run or {}).get("agent_id") or "")
            if agent_id_for_run:
                from agents import registry as _registry
                spec = _registry.get_agent(agent_id_for_run)
                from memory.binding import effective_memory_pools
                _mem_pools = effective_memory_pools(spec, (run or {}).get("workspace")) if spec else []
                if _mem_pools:
                    from memory.tool import silent_task_episode
                    silent_task_episode(
                        _mem_pools[0],
                        agent_id_for_run,
                        task_id=str(task_id_str),
                        run_id=run_id,
                        status=status,
                        exit_code=exit_code,
                        error=str((run or {}).get("error") or "") or None,
                        workspace=str((run or {}).get("workspace") or "") or None,
                    )
        except Exception:
            pass

        if status == "completed":
            # Only advance to 'resolved' if the agent didn't already set a
            # terminal status itself (e.g. code_reviewer sets 'reviewed' or 'blocked').
            # Orchestrator and code_reviewer are not workers — they must not set 'resolved'.
            agent_id_for_run = str((run or {}).get("agent_id") or "")
            non_resolving_agents = {"orchestrator", "code_reviewer"}
            agent_set_statuses = {
                _ts.TaskStatus.reviewing,
                _ts.TaskStatus.reviewed,
                _ts.TaskStatus.blocked,
                _ts.TaskStatus.done,
            }
            if agent_id_for_run not in non_resolving_agents:
                current_task = _ts.get_task(tid)
                if current_task and current_task.status not in agent_set_statuses:
                    ws_name = str(getattr(current_task, "workspace", "") or "default")
                    try:
                        from workspace import get_workspace_metadata
                        followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                    except Exception:
                        followup_mode = "single"
                    if followup_mode == "continuous":
                        # Keep in_progress so the UI shows work is ongoing, but clear
                        # the agent assignment (agent_state=none) so the orchestrator's
                        # _followup filter picks it up to decide the next step.
                        _ts.update_task(tid, status=_ts.TaskStatus.in_progress)
                        _ts.clear_agent(tid)
                    else:
                        _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                        _ts.clear_agent(tid)
            elif agent_id_for_run == "orchestrator":
                # Orchestrator is a non-resolving agent: it must set the task
                # status itself via update_task when the work is complete. But a
                # continuation ([MONITOR]) orchestrator run is the *last* actor on
                # the task — if it ends without acting (small models sometimes
                # narrate "marking as resolved" without calling the tool), nothing
                # else will ever move the task. Deterministic fallback: a completed
                # continuation run that still owns the assignment resolves an
                # in_progress task and always releases the stale assignment.
                if str((run or {}).get("channel") or "") == "continuation":
                    current_task = _ts.get_task(tid)
                    if current_task and str(getattr(current_task, "assigned_agent_run_id", "") or "") == str(run_id):
                        if current_task.status == _ts.TaskStatus.in_progress:
                            _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                            _ts.append_task_activity_log(
                                tid,
                                "continuation_fallback",
                                "Continuation orchestrator finished without setting a task status — auto-resolved",
                                run_id=run_id,
                            )
                        _ts.clear_agent(tid)
                        # The continuation is the last LLM actor: if the task
                        # ended up resolved and the orchestrator did not chain a
                        # reviewer itself, start the review deterministically so
                        # the pipeline (resolved → reviewing → reviewed → done →
                        # next subtask) never stalls on a skipped tool call.
                        current_task = _ts.get_task(tid)
                        if current_task and current_task.status == _ts.TaskStatus.resolved:
                            _auto_start_review(tid, current_task)
            elif agent_id_for_run == "code_reviewer":
                # Deterministic review outcome handling. The reviewer records its
                # verdict itself via tools (reviewed / blocked); finalize turns
                # that verdict into pipeline progress instead of relying on yet
                # another orchestrator turn:
                #   reviewed  → done (releases dependents, dispatches the next
                #               container subtask, may complete the parent);
                #   reviewing → the reviewer never recorded a verdict — park the
                #               task back at resolved for the user (no auto-retry,
                #               so a silent reviewer cannot cause a review loop);
                #   blocked   → keep the block for a fix cycle.
                # In every case release the reviewer's stale assignment. Only
                # active in continuous followup mode — in single mode the user
                # inspects the verdict and decides the next step themselves.
                current_task = _ts.get_task(tid)
                ws_name = str(getattr(current_task, "workspace", "") or "default") if current_task else "default"
                try:
                    from workspace import get_workspace_metadata
                    followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                except Exception:
                    followup_mode = "single"
                if (
                    followup_mode == "continuous"
                    and current_task
                    and str(getattr(current_task, "assigned_agent_run_id", "") or "") == str(run_id)
                ):
                    if current_task.status == _ts.TaskStatus.reviewed:
                        _ts.update_task(tid, status=_ts.TaskStatus.done)
                        _ts.append_task_activity_log(
                            tid,
                            "review_passed",
                            "Review passed — task finalized as done",
                            run_id=run_id,
                            agent_id="code_reviewer",
                        )
                    elif current_task.status == _ts.TaskStatus.reviewing:
                        _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                        _ts.append_task_activity_log(
                            tid,
                            "review_no_verdict",
                            "Reviewer finished without recording a verdict — task returned to resolved",
                            run_id=run_id,
                            agent_id="code_reviewer",
                        )
                    _ts.clear_agent(tid)
        else:
            error_msg = str((run or {}).get("error") or "").strip()
            reason = error_msg or f"process exited with code {exit_code}"
            # Retry policy: re-dispatch instead of blocking when the workspace
            # allows it and this task hasn't exhausted its retries. A dispatched
            # retry leaves the task in_progress, so skip blocking + continuations.
            if _maybe_retry_failed_run(tid, run or {}, reason):
                return
            _ts.block_task(tid, reason=reason)
            _ts.clear_agent(tid)
            # Surface the error in the activity log so the task page shows it
            try:
                agent_id_str = str((run or {}).get("agent_id") or "")
                _ts.append_task_activity_log(
                    tid,
                    "run_failed",
                    f"Run failed: {reason}",
                    run_id=run_id,
                    agent_id=agent_id_str,
                    exit_code=exit_code,
                )
            except Exception:
                pass

        # Trigger session continuations bound to this run (run-bound entries
        # ignore other runs on the task, e.g. the orchestrator's own run).
        try:
            from common.session_service import pop_continuations_for_task
            continuations = pop_continuations_for_task(str(task_id_str), run_id=run_id)
            for cont in continuations:
                try:
                    _trigger_session_continuation(cont, status)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def _trigger_session_continuation(cont: dict, finished_status: str) -> None:
    """Spawn the orchestrator as a background subprocess to continue the session."""
    import subprocess
    import sys
    from pathlib import Path as _Path

    task_id = cont.get("task_id")
    session_id = cont.get("session_id")
    workspace = cont.get("workspace") or ""
    agent_id = cont.get("agent_id", "orchestrator")
    if not task_id or not session_id:
        return

    project_root = _Path(__file__).resolve().parents[2]
    env = dict(__import__("os").environ)
    env["AGENT_SESSION_ID"] = session_id
    if workspace:
        env["AGENT_WORKSPACE"] = workspace

    # Pre-register the orchestrator run and assign it to the task so it is
    # visible in the UI as the active agent during continuation processing.
    run_id = str(uuid4())
    log_file = run_log_path(run_id)
    _upsert_run({
        "run_id": run_id,
        "task_id": str(task_id),
        "agent_id": agent_id,
        "status": "pending",
        "session_type": "task",
        "channel": "continuation",
        "session_id": session_id,
        "log_file": str(log_file),
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
    })
    try:
        from tasks import service as _ts
        from uuid import UUID as _UUID
        _ts.assign_agent(_UUID(task_id), agent_id, run_id=run_id)
        # Keep the UI showing ongoing work, but never overwrite a verdict the
        # finishing run already set — the continuation orchestrator needs to see
        # 'reviewed'/'blocked' to decide between done, fix and resolve.
        _verdict_statuses = {
            _ts.TaskStatus.reviewed,
            _ts.TaskStatus.blocked,
            _ts.TaskStatus.done,
            _ts.TaskStatus.stopped,
        }
        _current = _ts.get_task(_UUID(task_id))
        if _current and _current.status not in _verdict_statuses:
            _ts.update_task(_UUID(task_id), status=_ts.TaskStatus.in_progress)
    except Exception:
        pass

    env["AGENT_LOG_FILE"] = str(log_file)  # reuse the unified run_logs/ file
    env["AGENT_RUN_CHANNEL"] = "continuation"

    # Build a minimal instruction file so the agent gets context.
    # [MONITOR] prefix tells the orchestrator to skip Steps 1-3 and go directly
    # to Step 4 (get_agent_status_tool) so it does not re-assign the same task.
    instruction = (
        f"[MONITOR] Task ID: {task_id}\n\n"
        "A previously started agent has finished. "
        "Skip Steps 1-3. Go directly to Step 4 of your instructions."
    )
    args = [
        sys.executable, str(project_root / "runtime" / "agent_run.py"),
        agent_id,       # positional: agent
        instruction,    # positional: action
        "--task-id", task_id,
        "--run-id", run_id,
    ]
    if workspace:
        args += ["--workspace", workspace]

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(f"[continuation] task={task_id} session={session_id} trigger_status={finished_status}\n\n")
        subprocess.Popen(
            args,
            cwd=str(project_root),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _delete_runs_by_task_status(task_id: str, status: str) -> bool:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE task_id = ? AND status = ?",
            (str(task_id), status)).fetchall()
        if not rows:
            return False
        for r in rows:
            conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (r["run_id"],))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (r["run_id"],))
    return True


def delete_awaiting_approval_run(task_id: str) -> bool:
    """Delete the awaiting_approval run record for a task. Returns True if one was removed."""
    return _delete_runs_by_task_status(task_id, "awaiting_approval")


def delete_assigned_run(task_id: str) -> bool:
    """Delete the assigned (node-queued) run record for a task. Returns True if one was removed."""
    return _delete_runs_by_task_status(task_id, "assigned")
