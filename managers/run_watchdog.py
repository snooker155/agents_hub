"""
Background watchdog for agent runs that can never finish on their own.

Started once at FastAPI startup (same asyncio pattern as plans.scheduler).
Each tick scans the ``runs`` table for two failure shapes that otherwise stay
invisible until a human notices a frozen task:

1. **Stale pending runs** — ``assign_agent_tool`` pre-registers a run as
   ``pending``; the orchestrator is then supposed to call ``start_agent_tool``.
   Small models sometimes end their turn in between (observed failure: the
   model emits the start call as literal ``<tool_call>`` text inside its
   reasoning channel, where nothing executes), leaving a run that no component
   will ever start. The assignment itself is complete and valid, so after
   ``PENDING_AUTOSTART_SECONDS`` the watchdog performs the missing start
   itself — the same ``agent_launcher.start_run`` call ``start_agent_tool``
   would have made, including continuation registration so the downstream
   pipeline (resolve → review → done → next subtask) still fires. Only when
   auto-start is impossible (inconsistent state) or fails does the run get
   failed at ``PENDING_TIMEOUT_SECONDS`` through the normal failure path.

2. **Running runs whose process died** — a crash/kill/reboot between
   ``open_run`` and ``close_run`` leaves status ``running`` with a dead pid
   forever. ``get_status`` already auto-corrects this *when queried*; the
   watchdog does it proactively so continuations and task state recover
   without anyone opening the run in the UI.

Both shapes are closed via ``finalize_task_from_run`` so task blocking,
activity logging, and continuation triggering reuse the one tested path.
The task itself is only touched when it still points at the dead run.

Runs inside a resident instance's carrier (``carrier_run`` on the record, or
a ``node_id`` from before nodes were folded into instances) are exempt: the
carrier owns them, and when it dies ``instances.carrier.sync`` closes them
through ``fail_in_progress_runs_for_instance``.

The same tick also sweeps one level up, over instances: it reconciles copies
whose carrier process died, trims archived history past the per-workspace
retention limit, and delivers messages that were written to a copy while it was
busy (``instances.delivery.drain_idle``) — none of which has anywhere else to
happen once the copy stopped running.

Flow, loop, team and scenario runs are swept together, in one pass over
:mod:`common.entity_runs` (:func:`_sweep_entity_runs`), because since stage 0 of
the September 2026 plan they are one table with one status vocabulary
(``common/run_status.py``): every kind is a process with a lease, a heartbeat
and a checkpoint, whatever store its own adapter (flow.run_store, loops.store,
teams.store, playground.store) wraps it in. For every one of them death is not
the end: each writes a checkpoint as it goes, so a run whose process is gone is
**resumed** from it rather than failed, through the kind's own resumer
(flow.launcher.resume_flow_run, loops.launcher.resume_loop_run,
teams.launcher.resume_team_run, playground.launcher.resume_scenario_run, each
a relaunch of the kind's own process under the same run id). Only
a run with no checkpoint (for a loop, one with no ``iterations_done`` yet), or
one that has already been resumed :data:`MAX_AUTO_RESUMES` times, is closed as
failed: a run that cannot get past its next step must not be restarted
forever. Liveness is the run's ``heartbeat_at`` at a per-kind stale threshold
(``FLOW_``/``LOOP_``/``TEAM_``/``SCENARIO_HEARTBEAT_STALE_SECONDS``), refreshed
every node/round/tick and at least every 15 seconds while an agent step runs,
because a pid says only that *a* process exists: the pid of a crashed-and-reused
number is alive, and a hung orchestrator's pid is alive too, while a heartbeat
that stopped moving is the run itself going quiet. A record with no heartbeat at
all (older than heartbeats, or a process that died before its first beat) falls
back to :func:`runtime.entity_launch.child_alive`, which is ``None`` (leave it
alone) for a run this host did not start.

A pending entity run follows the same two rules an agent run's ``queued`` status
does: a queue row (``common/run_queue.py``) that failed or vanished means no
worker will ever start it, so it fails now; one that never touched the queue at
all (the default role launches synchronously, so this is the rare true orphan)
gets :data:`PENDING_TIMEOUT_SECONDS` before the same fate. A run parked
``awaiting_input`` is untouched either way: ``entity_runs.list_runs(active=True)``
never returns it (see ``run_status.PARKED_STATUSES``), because it is not a run
that died, it is one waiting on a person.

:func:`_sweep_flow_runs` and :func:`_sweep_loop_runs` still exist, as thin
one-kind wrappers over :func:`_sweep_entity_runs`, because the tests that
exercise flow and loop resume (``tests/test_flow_resume.py``,
``tests/test_loop_resume.py``) call them by name.

Agent runs carry the same sign of life since stage 2 of the scaling plan:
``runtime/agent_run.py`` refreshes ``runs.heartbeat_at`` every few seconds
through its state transport, so a run launched by a worker on another host is
judged by its heartbeat, never by a pid this process cannot see. The pid and
container probes remain for records without a heartbeat (older runs, or a
process that died before its first beat) and only on the host that started
them (``host`` on the record). A dead run with a checkpoint
(``run_payloads.checkpoint``) is put back on its way with ``--resume-checkpoint``
instead of failed, up to :data:`MAX_AUTO_RESUMES` times, the same rule flows
and loops follow.

With several backend replicas only the holder of the ``watchdog`` lease
(``common/leases.py``) sweeps; the others tick and try to take the lease. The
sweep also reconciles the launch queue (``common/run_queue.py``): rows whose
worker stopped renewing are closed or handed back.

Each sweep runs under the lease version the tick took (a fencing token,
``common.leases.fencing_token``). Every write that closes or resumes a run
checks it inside its own transaction; a watchdog that stalled past its TTL
and was superseded meets ``LeaseLost`` at its next write and ends the sweep
there, leaving the rest to the new holder.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Tuple

from common import leases

log = logging.getLogger("managers.run_watchdog")

TICK_SECONDS = float(os.environ.get("RUN_WATCHDOG_TICK_SECONDS", "60"))
# Grace before the watchdog starts an assigned-but-unstarted run itself: long
# enough for the orchestrator's own start_agent_tool call (one LLM step, which
# can take a few minutes on a busy local model), short enough that the pipeline
# barely stalls when that call never comes.
PENDING_AUTOSTART_SECONDS = float(os.environ.get("RUN_PENDING_AUTOSTART_SECONDS", "180"))
# Hard deadline: a pending run that could not be auto-started is failed.
PENDING_TIMEOUT_SECONDS = float(os.environ.get("RUN_PENDING_TIMEOUT_SECONDS", "900"))
# A flow run's heartbeat is refreshed every node and at least every 15s inside a
# node, so several missed beats mean the process is gone, not merely busy.
FLOW_HEARTBEAT_STALE_SECONDS = float(os.environ.get("FLOW_HEARTBEAT_STALE_SECONDS", "180"))
# A loop beats once per flow node of the iteration it is running; the threshold
# is wider because a single node of a single iteration can legitimately be slow.
LOOP_HEARTBEAT_STALE_SECONDS = float(os.environ.get("LOOP_HEARTBEAT_STALE_SECONDS", "900"))
# A team beats every RUN_HEARTBEAT_SECONDS like an agent run (teams/runner.py
# turns are agent runs of their own); a plain default, generous enough that a
# slow member turn does not read as a dead run.
TEAM_HEARTBEAT_STALE_SECONDS = float(os.environ.get("TEAM_HEARTBEAT_STALE_SECONDS", "180"))
# A scenario beats once per tick, the same shape as a team's rounds.
SCENARIO_HEARTBEAT_STALE_SECONDS = float(os.environ.get("SCENARIO_HEARTBEAT_STALE_SECONDS", "180"))
# How many times the watchdog may resume the same run by itself.
MAX_AUTO_RESUMES = int(os.environ.get("RUN_MAX_AUTO_RESUMES", "2"))
# An agent run beats every RUN_HEARTBEAT_SECONDS (runtime/agent_run.py); a run
# quiet for this long is gone. Generous, because a beat is one database write
# that may itself wait on a busy database.
RUN_HEARTBEAT_STALE_SECONDS = float(os.environ.get("RUN_HEARTBEAT_STALE_SECONDS", "180"))
# The lease role this loop runs under, and its length.
LEASE_ROLE = "watchdog"

# The (role, token) the running sweep is fenced by, None when unfenced (tests
# and callers that sweep outside the loop). A context variable rather than a
# parameter threaded through every helper: sweep_once runs in a worker
# thread per tick and sets it for that call only.
_fence: contextvars.ContextVar[Optional[Tuple[str, int]]] = contextvars.ContextVar(
    "run_watchdog_fence", default=None)


@contextmanager
def _fenced_write() -> Iterator[None]:
    """Wrap one closing write in the sweep's fence: the lease is checked
    inside the write's transaction and ``LeaseLost`` is raised before it."""
    fence = _fence.get()
    if fence is None:
        yield
        return
    with leases.fenced(*fence):
        yield


def _check_fence() -> None:
    """Raise ``LeaseLost`` when the sweep's fence no longer holds. For the
    steps that launch a process, which cannot sit inside a transaction."""
    fence = _fence.get()
    if fence is not None and not leases.verify(*fence):
        raise leases.LeaseLost(f"lease {fence[0]} is no longer held under version {fence[1]}")


def _age_seconds(iso_ts: str) -> Optional[float]:
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def _task_owns_run(task_id: str, run_id: str) -> bool:
    try:
        from tasks import service as _ts
        from uuid import UUID
        task = _ts.get_task(UUID(task_id))
        return bool(task) and str(getattr(task, "assigned_agent_run_id", "") or "") == run_id
    except Exception:  # noqa: BLE001 - a lookup failure means "not confirmed owned", the safer default
        log.debug("_task_owns_run failed for task %s", task_id, exc_info=True)
        return False


def _carried_here(rec: Dict[str, Any]) -> bool:
    """Whether this process may probe the run's pid or container: the record
    names no host (an older record) or names this one."""
    import socket
    host = str(rec.get("host") or "")
    return not host or host == socket.gethostname()


def _run_is_dead(rec: Dict[str, Any]) -> Optional[bool]:
    """True when a running agent run's process is gone, False when it is
    alive, None when this process cannot tell (another host, no heartbeat)."""
    from managers import run_manager as rm

    heartbeat = str(rec.get("heartbeat_at") or "")
    if heartbeat:
        age = _age_seconds(heartbeat)
        if age is None:
            return None
        return age > RUN_HEARTBEAT_STALE_SECONDS
    if not _carried_here(rec):
        return None
    container_name = rec.get("container_name")
    if container_name:
        # A container-hosted run: liveness is the container, not a pid — the
        # container's own agent_run.py runs with a pid that only means
        # something inside its own namespace. See the comment on
        # run_manager._stop_run_record for why container_name (not
        # execution_mode) is what identifies one.
        from managers.container_manager import container_running
        return not container_running(str(container_name))
    pid = int(rec.get("pid") or 0)
    if pid > 0:
        return not rm._pid_exists(pid)
    return None


def _try_resume_from_checkpoint(rec: Dict[str, Any]) -> bool:
    """Relaunch a dead run from its checkpoint (agents/checkpoint.py), under
    the same run id. False when there is no checkpoint, the cap is reached,
    the task moved on, or the launch itself fails."""
    from managers import run_manager as rm

    run_id = str(rec.get("run_id") or "")
    task_id = str(rec.get("task_id") or "")
    agent_id = str(rec.get("agent_id") or "")
    attempts = int(rec.get("resume_attempts") or 0)
    if not run_id or not task_id or not agent_id or attempts >= MAX_AUTO_RESUMES:
        return False
    try:
        from agents.checkpoint import load_checkpoint
        checkpoint = load_checkpoint(run_id)
    except Exception:  # noqa: BLE001 - best-effort (see docstring): a load failure just means no auto-resume
        log.debug("load_checkpoint failed for run %s", run_id, exc_info=True)
        return False
    if not checkpoint or not checkpoint.get("steps"):
        return False
    if not _task_owns_run(task_id, run_id):
        return False
    try:
        from uuid import UUID
        from tasks import service as _ts
        task = _ts.get_task(UUID(task_id))
        if task is None or task.status in (_ts.TaskStatus.stopped, _ts.TaskStatus.done,
                                           _ts.TaskStatus.blocked):
            return False
        params = dict(getattr(task, "assigned_agent_params", None) or {})
        params["resume_checkpoint"] = run_id
        with _fenced_write():
            rm.update_run(run_id, {"resume_attempts": attempts + 1, "heartbeat_at": None})
        from agents import agent_launcher
        agent_launcher.start_run(task_id, agent_id, params, run_id=run_id)
        _ts.append_task_activity_log(
            UUID(task_id), "watchdog_resume",
            f"Run process died after step {checkpoint.get('step')}; resumed from its checkpoint "
            f"(attempt {attempts + 1} of {MAX_AUTO_RESUMES})",
            run_id=run_id, agent_id=agent_id,
        )
        log.warning("watchdog resumed run %s (%s) from its checkpoint (attempt %d)",
                    run_id[:8], agent_id, attempts + 1)
        return True
    except leases.LeaseLost:
        raise
    except Exception:
        log.exception("watchdog could not resume run %s", run_id[:8])
        return False


def _fail_run(rec: Dict[str, Any], error: str) -> None:
    """Close a dead run; route through finalize only when the task still
    points at it (a superseded run must not block the task's current state)."""
    from managers import run_manager as rm

    run_id = str(rec.get("run_id") or "")
    task_id = str(rec.get("task_id") or "")
    with _fenced_write():
        rm.update_run(run_id, {
            "status": "failed",
            "finished_at": rm.utc_now_iso(),
            "error": error,
            "exit_code": rec.get("exit_code"),
        })
    log.warning("watchdog failed run %s (%s): %s", run_id[:8], rec.get("agent_id"), error)
    if task_id and _task_owns_run(task_id, run_id):
        rm.finalize_task_from_run(run_id, "failed", 1)


def _try_autostart(rec: Dict[str, Any]) -> bool:
    """Start an assigned-but-unstarted run exactly as start_agent_tool would.

    Returns True when the worker subprocess was launched. Refuses (False) when
    the state is inconsistent: the task no longer points at this run, the
    assigned agent changed, the task reached a terminal state, or the
    workspace is in node execution mode (node runs use 'assigned', not
    'pending', so this is just belt-and-braces).
    """
    from uuid import UUID
    from tasks import service as _ts

    run_id = str(rec.get("run_id") or "")
    task_id = str(rec.get("task_id") or "")
    agent_id = str(rec.get("agent_id") or "")
    if not run_id or not task_id or not agent_id:
        return False
    _check_fence()
    try:
        task = _ts.get_task(UUID(task_id))
        if (
            task is None
            or str(getattr(task, "assigned_agent_run_id", "") or "") != run_id
            or str(getattr(task, "assigned_agent_type", "") or "") != agent_id
            or task.status in (_ts.TaskStatus.stopped, _ts.TaskStatus.done, _ts.TaskStatus.blocked)
        ):
            return False

        from workspace import get_workspace_metadata
        ws_name = str(getattr(task, "workspace", "") or "default")
        orch = get_workspace_metadata(ws_name).get("orchestrator", {})
        if orch.get("execution_mode", "subprocess") == "node":
            return False

        from agents import agent_launcher
        _rid, session_id = agent_launcher.start_run(
            task_id, agent_id, getattr(task, "assigned_agent_params", None), run_id=run_id
        )
        _ts.update_task(UUID(task_id), status=_ts.TaskStatus.in_progress)
        _ts.append_task_activity_log(
            UUID(task_id),
            "watchdog_autostart",
            f"Assigned run was never started by the orchestrator — watchdog started {agent_id}",
            run_id=run_id,
            agent_id=agent_id,
        )
        # Mirror start_agent_tool: in continuous fire-and-forget mode the
        # finished worker must trigger a follow-up orchestrator, or the task
        # would park at in_progress with no agent after finalize.
        if orch.get("followup_mode", "single") == "continuous" and not orch.get("wait_for_completion", False):
            if session_id:
                from common.session_service import register_continuation
                register_continuation(
                    session_id=str(session_id),
                    task_id=task_id,
                    workspace=ws_name,
                    run_id=run_id,
                )
        log.warning("watchdog auto-started run %s (%s) on task %s", run_id[:8], agent_id, task_id[:8])
        return True
    except Exception:
        log.exception("watchdog auto-start failed for run %s", run_id[:8])
        return False


def sweep_once(fence: Optional[Tuple[str, int]] = None) -> int:
    """Scan all runs once; returns the number of runs recovered or closed.

    ``fence`` is ``(role, token)`` from the watchdog lease: every closing
    write checks it in its own transaction, and the sweep ends at the first
    write that finds the lease gone (``LeaseLost``), returning what it
    handled so far.
    """
    reset = _fence.set(fence)
    counter = [0]
    try:
        _sweep(counter)
    except leases.LeaseLost:
        log.warning("watchdog lost the %s lease mid-sweep, stopping after %d run(s)",
                    fence[0] if fence else LEASE_ROLE, counter[0])
    finally:
        _fence.reset(reset)
    return counter[0]


def _sweep(counter: list) -> None:
    """The body of :func:`sweep_once`; ``counter[0]`` survives a
    ``LeaseLost`` raised halfway."""
    from managers import run_manager as rm

    for rec in rm.load_runs():
        status = str(rec.get("status") or "")
        if rec.get("carrier_run") or rec.get("node_id"):
            continue

        if status == "pending":
            age = _age_seconds(str(rec.get("created_at") or ""))
            if age is None:
                continue
            if age > PENDING_AUTOSTART_SECONDS and _try_autostart(rec):
                counter[0] += 1
            elif age > PENDING_TIMEOUT_SECONDS:
                _fail_run(rec, (
                    f"Run was assigned but never started within "
                    f"{int(PENDING_TIMEOUT_SECONDS // 60)} minutes, and auto-start "
                    f"did not succeed. Reassign the agent to retry."
                ))
                counter[0] += 1

        elif status == "running":
            if _run_is_dead(rec):
                if _try_resume_from_checkpoint(rec):
                    counter[0] += 1
                elif rec.get("heartbeat_at"):
                    _fail_run(rec, (
                        "Run stopped reporting a heartbeat "
                        f"(quiet for more than {int(RUN_HEARTBEAT_STALE_SECONDS)}s): "
                        "its process is gone without finalizing."
                    ))
                    counter[0] += 1
                elif rec.get("container_name"):
                    _fail_run(rec, "Run container exited without finalizing (crash or external kill).")
                    counter[0] += 1
                else:
                    _fail_run(rec, "Run process died without finalizing (crash or external kill).")
                    counter[0] += 1

        elif status == "stop":
            counter[0] += _settle_stop(rec)

        elif status == "queued":
            counter[0] += _check_queued_run(rec)

    _check_fence()
    counter[0] += _sweep_queue()
    _sweep_containers()
    counter[0] += _sweep_entity_runs()
    _check_fence()
    counter[0] += _sweep_instances()


def _settle_stop(rec: Dict[str, Any]) -> int:
    """A stop request (``stop``) nobody saw through. A run stopped in
    process gets ``stop`` and its finish time together and its turn aborts
    at the next model or tool boundary, so once :data:`PENDING_TIMEOUT_SECONDS`
    passed since that finish it is over. A run in its own process is over once
    that process is gone. Either way the record reads ``stopped`` from then
    on; before this, nothing but a lookup of that very run (``get_status``)
    ever moved it, so a stop whose process died unseen stayed ``stop`` for
    good and counted as running."""
    from managers import run_manager as rm

    finished = str(rec.get("finished_at") or "")
    if finished:
        age = _age_seconds(finished)
        if age is None or age <= PENDING_TIMEOUT_SECONDS:
            return 0
    elif not _run_is_dead(rec):
        return 0
    run_id = str(rec.get("run_id") or "")
    updates: Dict[str, Any] = {"status": "stopped", "finished_at": finished or rm.utc_now_iso(),
                               "exit_code": rec.get("exit_code") if rec.get("exit_code") is not None else 0}
    if not finished:
        # When the process died is unknown, so the finish time is this
        # sweep's: lists of what ended lately leave such a run out.
        updates["settled_by"] = "watchdog"
    with _fenced_write():
        rm.update_run(run_id, updates)
    log.warning("watchdog settled stopped run %s (%s)", run_id[:8], rec.get("agent_id"))
    return 1


def _check_queued_run(rec: Dict[str, Any]) -> int:
    """A run waiting for a worker. The queue row is the truth about it: gone
    or failed means no worker will ever start it, so the run fails now."""
    from common import run_queue

    run_id = str(rec.get("run_id") or "")
    try:
        row = run_queue.get(run_id)
    except Exception:  # noqa: BLE001 - falls back to leaving the run alone this pass
        log.debug("run_queue.get failed for %s", run_id, exc_info=True)
        return 0
    if row is None:
        _fail_run(rec, "Run was queued for a worker but its queue entry is gone.")
        return 1
    if row.get("status") == run_queue.STATUS_FAILED:
        _fail_run(rec, "No worker could start this run: "
                  + str(row.get("last_error") or "launch failed"))
        return 1
    return 0


def _sweep_queue() -> int:
    """Close or hand back launch rows whose worker stopped renewing."""
    try:
        from common import run_queue
        done = run_queue.sweep()
    except Exception:
        log.exception("run queue sweep failed")
        return 0
    n = int(done.get("requeued", 0)) + int(done.get("failed", 0))
    if n or done.get("closed"):
        log.info("run queue sweep: %s", done)
    return n


def _sweep_containers() -> None:
    """Keep this host's rows in the ``containers`` table honest: a container
    the daemon no longer has is dropped, an exited one is marked. Cheap when
    this host registered nothing (no daemon call at all)."""
    try:
        from managers.container_manager import refresh_registered_containers
        refresh_registered_containers()
    except Exception:  # noqa: BLE001 - best-effort sweep, must not break the rest of the watchdog tick
        log.debug("container registry sweep failed", exc_info=True)


def _flow_run_is_dead(rec: Dict[str, Any]) -> bool:
    """True when a running flow run's process is gone.

    The heartbeat decides when there is one. A record written before heartbeats
    existed has none, and for those the old pid probe is still the best signal
    available — being wrong about an old run is worse than being late about it.
    """
    from managers import run_manager as rm

    heartbeat = str(rec.get("heartbeat_at") or "")
    if heartbeat:
        age = _age_seconds(heartbeat)
        return age is not None and age > FLOW_HEARTBEAT_STALE_SECONDS
    if not _carried_here(rec):
        return False
    pid = int(rec.get("pid") or 0)
    return pid > 0 and not rm._pid_exists(pid)


# ── The one sweep over every entity kind ────────────────────────────────────
# flow, loop, team and scenario runs all live in common/entity_runs.py now,
# one status vocabulary (common/run_status.py) and one shape of liveness: a
# heartbeat, a checkpoint, a resume_attempts counter. This section replaced
# the old per-kind _sweep_flow_runs/_sweep_loop_runs bodies (kept below as
# one-kind wrappers) with a single pass, generalised the same way sweep_once
# already generalises pending/running agent runs.

#: Per-kind stale threshold, checked against ``heartbeat_at``. Falls back to
#: :data:`RUN_HEARTBEAT_STALE_SECONDS` for a kind not listed (there is none
#: today; this is only ever missing on a programming error).
_STALE_SECONDS: Dict[str, float] = {
    "flow": FLOW_HEARTBEAT_STALE_SECONDS,
    "loop": LOOP_HEARTBEAT_STALE_SECONDS,
    "team": TEAM_HEARTBEAT_STALE_SECONDS,
    "scenario": SCENARIO_HEARTBEAT_STALE_SECONDS,
}


def _resume_flow_run(rec: Dict[str, Any]) -> None:
    from flow.launcher import resume_flow_run
    resume_flow_run(str(rec["run_id"]), auto=True)


def _resume_loop_run(rec: Dict[str, Any]) -> None:
    # A loop is a process of its own since stage 0a (loops/launcher.py), so a
    # resume is a relaunch under the same id, spawned here or queued for a
    # worker, never a thread of this process.
    from loops.launcher import resume_loop_run
    resume_loop_run(str(rec["run_id"]), auto=True)


def _resume_team_run(rec: Dict[str, Any]) -> None:
    from teams.launcher import resume_team_run
    resume_team_run(str(rec["run_id"]), auto=True)


def _resume_scenario_run(rec: Dict[str, Any]) -> None:
    from playground.launcher import resume_scenario_run
    resume_scenario_run(str(rec["run_id"]), auto=True)


#: Per kind: how to relaunch a dead run from its checkpoint. Every resumer
#: takes the entity_runs record and is responsible for its own bookkeeping
#: (resume_attempts, the checkpoint, the process); the sweep only decides
#: *whether* to call it.
_RESUMERS: Dict[str, Callable[[Dict[str, Any]], None]] = {
    "flow": _resume_flow_run,
    "loop": _resume_loop_run,
    "team": _resume_team_run,
    "scenario": _resume_scenario_run,
}


def _entity_run_is_dead(rec: Dict[str, Any]) -> Optional[bool]:
    """True when a running/stopping entity run's process is gone, False when
    it is alive, None when this process cannot tell.

    Generalises :func:`_flow_run_is_dead` over every kind: the heartbeat
    decides when there is one, at the kind's own stale threshold
    (:data:`_STALE_SECONDS`). A record with no heartbeat at all falls back to
    :func:`runtime.entity_launch.child_alive`, which already knows how to
    read a pid or a container name, and already returns None for a run this
    host did not start, so a replica never guesses about another host's
    process the way an old pid-only record still gets a guess in
    :func:`_flow_run_is_dead`.
    """
    threshold = _STALE_SECONDS.get(str(rec.get("kind") or ""), RUN_HEARTBEAT_STALE_SECONDS)
    heartbeat = str(rec.get("heartbeat_at") or "")
    if heartbeat:
        age = _age_seconds(heartbeat)
        return None if age is None else age > threshold
    from runtime.entity_launch import child_alive
    alive = child_alive(rec)
    return None if alive is None else not alive


#: How long an active entity run with nothing to check it by may stand
#: before it counts as an orphan. Every launcher now writes a heartbeat (and
#: a pid or container) when the run starts, so only an older record lacks
#: all of them; the wait keeps a run caught between its insert and its first
#: write from ever being taken for one.
ORPHAN_ENTITY_SECONDS = float(os.environ.get("RUN_ORPHAN_SECONDS", "3600"))


def _entity_run_is_orphan(rec: Dict[str, Any]) -> bool:
    """An active entity run this host could never judge: no heartbeat, no
    pid, no container, and no other host named, started long ago. Resuming
    one is no use (whatever it was waiting on is long gone), so it is closed
    as failed instead of being skipped on every sweep forever."""
    if rec.get("heartbeat_at") or rec.get("container_name") or int(rec.get("pid") or 0) > 0:
        return False
    if not _carried_here(rec):
        return False
    age = _age_seconds(str(rec.get("started_at") or rec.get("created_at") or ""))
    return age is not None and age > ORPHAN_ENTITY_SECONDS


def _entity_checkpoint_resumable(rec: Dict[str, Any]) -> bool:
    """Whether this run's checkpoint is enough to resume from.

    Any non-empty checkpoint qualifies, except a loop's: its checkpoint *is*
    its position (loops/store.py), and a position written before the first
    iteration finished has nothing in it a resume could pick up from. The
    same guard ``loops.launcher.resume_loop_run`` applies itself, checked here
    first so the sweep does not even try.
    """
    checkpoint = rec.get("checkpoint") or {}
    if not checkpoint:
        return False
    if str(rec.get("kind") or "") == "loop":
        return bool(checkpoint.get("iterations_done"))
    return True


def _fail_entity_run(rec: Dict[str, Any], error: str, *, stop_reason: str = "error") -> None:
    """Close a dead entity run as failed and finalize its task, generically
    for every kind. Flow keeps its own close (it mirrors the log file and
    clears the flow's coarse running marker); every other kind goes through
    the shared :func:`common.entity_runs.close`."""
    from common import entity_runs

    kind = str(rec.get("kind") or "")
    run_id = str(rec.get("run_id") or "")
    try:
        if kind == "flow":
            from flow import run_store as _flow_run_store
            from flow.launcher import _set_flow_running
            with _fenced_write():
                _flow_run_store.close_flow_run(run_id, status="failed", exit_code=1, error=error)
            _set_flow_running(str(rec.get("flow_id") or rec.get("entity_id") or ""), False)
        else:
            with _fenced_write():
                entity_runs.close(run_id, status="failed", exit_code=1, error=error,
                                  stop_reason=stop_reason)
    except leases.LeaseLost:
        raise
    except Exception:
        log.exception("watchdog could not close %s run %s", kind, run_id[:8])
        return

    task_id = str(rec.get("task_id") or "")
    if task_id:
        try:
            from managers.run_manager import finalize_flow_task
            finalize_flow_task(task_id, "failed", 1, error=error)
        except Exception:
            log.exception("watchdog could not finalize task %s for run %s", task_id[:8], run_id[:8])
    log.warning("watchdog failed %s run %s: %s", kind, run_id[:8], error)


def _resume_or_fail_entity_run(rec: Dict[str, Any]) -> int:
    """A dead run: resume it from its checkpoint when the budget allows,
    otherwise fail it. Returns 1 either way (the caller counts runs handled,
    not outcomes)."""
    kind = str(rec.get("kind") or "")
    run_id = str(rec.get("run_id") or "")
    status = str(rec.get("status") or "")
    attempts = int(rec.get("resume_attempts") or 0)
    checkpoint_ok = _entity_checkpoint_resumable(rec)

    # A run already asked to stop is not resumed even if it still has budget
    # left: honouring the stop is the point, and the transition table agrees
    # (stopping -> running is not a legal move, common/run_status.py). Only
    # a run still trying to make forward progress gets another attempt.
    if status == "running" and checkpoint_ok and attempts < MAX_AUTO_RESUMES:
        resumer = _RESUMERS.get(kind)
        if resumer is not None:
            try:
                # The record says running but the process is gone: say so
                # before relaunching. The launchers' resume accepts a stopped
                # or failed run only (a live one must never be relaunched on
                # top of itself), and running -> failed -> running is what
                # the transition table allows; the flow resumer moves the
                # record back to running itself.
                from common import entity_runs
                with _fenced_write():
                    entity_runs.update(run_id, {
                        "status": "failed", "finished_at": entity_runs.utc_now_iso(),
                        "error": "Run process stopped without finalizing; resuming from its checkpoint.",
                    }, notify=False)
                resumer(rec)
                log.warning(
                    "watchdog resumed %s run %s from its checkpoint (attempt %d)",
                    kind, run_id[:8], attempts + 1,
                )
                return 1
            except leases.LeaseLost:
                raise
            except Exception:
                log.exception("watchdog could not resume %s run %s", kind, run_id[:8])

    error = (
        "Run process stopped without finalizing"
        + (f" and could not be resumed after {attempts} attempt(s)."
           if checkpoint_ok else " and had no checkpoint to resume from.")
    )
    _fail_entity_run(rec, error)
    return 1


def _check_pending_entity_run(rec: Dict[str, Any]) -> int:
    """A pending entity run waiting for a worker. Mirrors :func:`_check_queued_run`
    for agent runs: a queue row that failed or disappeared means no worker will
    ever start it, so the run fails now. A run that never touched the queue at
    all (the default role launches synchronously, so this is the rare true
    orphan, not the common case) gets :data:`PENDING_TIMEOUT_SECONDS` before the
    same fate."""
    from common import run_queue

    run_id = str(rec.get("run_id") or "")
    try:
        row = run_queue.get(run_id)
    except Exception:  # noqa: BLE001 - falls back to leaving the run alone this pass
        log.debug("run_queue.get failed for %s", run_id, exc_info=True)
        return 0
    if row is not None:
        if row.get("status") == run_queue.STATUS_FAILED:
            _fail_entity_run(rec, "No worker could start this run: "
                             + str(row.get("last_error") or "launch failed"))
            return 1
        return 0
    age = _age_seconds(str(rec.get("created_at") or ""))
    if age is not None and age > PENDING_TIMEOUT_SECONDS:
        _fail_entity_run(rec, (
            f"Run was pending but never started within "
            f"{int(PENDING_TIMEOUT_SECONDS // 60)} minutes."
        ))
        return 1
    return 0


def _sweep_entity_runs(kinds: Optional[Iterable[str]] = None) -> int:
    """One sweep over flow, loop, team and scenario runs (``kinds=None``), or
    just one kind's slice of it: :func:`_sweep_flow_runs` and
    :func:`_sweep_loop_runs` are exactly that, kept for the tests that already
    call them by name.

    Every run in an active status (pending, running, stopping:
    ``common.run_status.ACTIVE_STATUSES``) is read once; a parked
    ``awaiting_input`` run is never in that set, so it is never visited here.
    """
    from common import entity_runs

    handled = 0
    for rec in entity_runs.list_runs(kinds=list(kinds) if kinds is not None else None,
                                     active=True):
        status = str(rec.get("status") or "")
        if status == "pending":
            handled += _check_pending_entity_run(rec)
            continue
        if status not in ("running", "stopping"):
            continue
        if _entity_run_is_orphan(rec):
            _fail_entity_run(rec, (
                "Run left without a process, a heartbeat or a host to ask: it was "
                "started by an older hub or before a restart and never finished."
            ), stop_reason="orphan")
            handled += 1
            continue
        if not _entity_run_is_dead(rec):
            continue
        handled += _resume_or_fail_entity_run(rec)
    return handled


def _sweep_flow_runs() -> int:
    """Flow's slice of :func:`_sweep_entity_runs`. Kept as a thin wrapper so
    tests/test_flow_resume.py keeps asserting against a flow-only sweep."""
    return _sweep_entity_runs(kinds=("flow",))


def _sweep_loop_runs() -> int:
    """Loop's slice of :func:`_sweep_entity_runs`. Kept as a thin wrapper so
    tests/test_loop_resume.py keeps asserting against a loop-only sweep."""
    return _sweep_entity_runs(kinds=("loop",))


def _sweep_instances() -> int:
    """Correct instances whose carrier died, then trim archived history.

    Same failure shape as a dead run, one level up: an instance that claims to
    be live forever is worse than a stale run, because the Instances page is
    where an operator goes to see what is actually working.
    """
    try:
        from instances import registry as instance_registry
        from instances import store as instance_store
    except ImportError:
        return 0
    try:
        fixed = instance_registry.reconcile()
    except Exception:
        log.exception("instance reconcile failed")
        fixed = 0
    try:
        for workspace in instance_store.workspaces_with_instances():
            instance_store.enforce_retention(workspace)
    except Exception:
        log.exception("instance retention sweep failed")
    return fixed


class RunWatchdog:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._leader = False

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="run-watchdog")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
        if self._leader:
            self._leader = False
            try:
                from common import leases
                await asyncio.to_thread(leases.release, LEASE_ROLE)
            except Exception:  # noqa: BLE001 - shutdown must not raise, the lease simply lapses on its own TTL
                log.debug("lease release failed during watchdog stop", exc_info=True)

    def holds_lease(self) -> bool:
        return self._leader

    async def _loop(self) -> None:
        from common import leases

        ttl = max(TICK_SECONDS * 3, leases.DEFAULT_TTL_SECONDS)
        while not self._stop.is_set():
            try:
                self._leader = await asyncio.to_thread(leases.hold, LEASE_ROLE, ttl)
                token = leases.fencing_token(LEASE_ROLE) if self._leader else None
                if self._leader:
                    fence = (LEASE_ROLE, token) if token is not None else None
                    closed = await asyncio.to_thread(sweep_once, fence)
                    if closed:
                        log.info("watchdog closed %d dead run(s)", closed)
            except Exception:
                log.exception("watchdog tick failed")
            # Messages written to a busy instance wait in its mailbox: the
            # subprocess that finishes its run cannot start a chat turn, so the
            # backend delivers them once the copy goes idle.
            try:
                from instances.delivery import drain_idle
                delivered = await drain_idle()
                if delivered:
                    log.info("delivered %d queued instance message(s)", delivered)
            except Exception:
                log.exception("instance mailbox drain failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass


watchdog = RunWatchdog()

__all__ = ["RunWatchdog", "watchdog", "sweep_once"]
