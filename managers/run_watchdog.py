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

Node-queued runs (status ``assigned``, or a ``node_id`` on the record) are
exempt: waiting long in a node queue is legitimate, and node liveness is
handled by ``fail_in_progress_runs_for_node``.

The same tick also sweeps one level up, over instances: it reconciles copies
whose carrier process died, trims archived history past the per-workspace
retention limit, and delivers messages that were written to a copy while it was
busy (``instances.delivery.drain_idle``) — none of which has anywhere else to
happen once the copy stopped running.

Flow and loop runs are swept too, and for them death is not the end: both write
a checkpoint as they go, so a run whose process is gone is **resumed** from it
rather than failed. Only a run with no checkpoint, or one that has already been
resumed :data:`MAX_AUTO_RESUMES` times, is closed as failed — a run that cannot
get past its next node must not be restarted forever. Liveness for a flow run
is its ``heartbeat_at``, refreshed every node and at least every 15 seconds
while an agent node runs, because a pid says only that *a* process exists: the
pid of a crashed-and-reused number is alive, and a hung orchestrator's pid is
alive too, while a heartbeat that stopped moving is the run itself going quiet.

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
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
# How many times the watchdog may resume the same run by itself.
MAX_AUTO_RESUMES = int(os.environ.get("RUN_MAX_AUTO_RESUMES", "2"))
# An agent run beats every RUN_HEARTBEAT_SECONDS (runtime/agent_run.py); a run
# quiet for this long is gone. Generous, because a beat is one database write
# that may itself wait on a busy database.
RUN_HEARTBEAT_STALE_SECONDS = float(os.environ.get("RUN_HEARTBEAT_STALE_SECONDS", "180"))
# The lease role this loop runs under, and its length.
LEASE_ROLE = "watchdog"


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
    except Exception:
        log.exception("watchdog could not resume run %s", run_id[:8])
        return False


def _fail_run(rec: Dict[str, Any], error: str) -> None:
    """Close a dead run; route through finalize only when the task still
    points at it (a superseded run must not block the task's current state)."""
    from managers import run_manager as rm

    run_id = str(rec.get("run_id") or "")
    task_id = str(rec.get("task_id") or "")
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


def sweep_once() -> int:
    """Scan all runs once; returns the number of runs recovered or closed."""
    from managers import run_manager as rm

    closed = 0
    for rec in rm.load_runs():
        status = str(rec.get("status") or "")
        if rec.get("node_id"):
            continue

        if status == "pending":
            age = _age_seconds(str(rec.get("created_at") or ""))
            if age is None:
                continue
            if age > PENDING_AUTOSTART_SECONDS and _try_autostart(rec):
                closed += 1
            elif age > PENDING_TIMEOUT_SECONDS:
                _fail_run(rec, (
                    f"Run was assigned but never started within "
                    f"{int(PENDING_TIMEOUT_SECONDS // 60)} minutes, and auto-start "
                    f"did not succeed. Reassign the agent to retry."
                ))
                closed += 1

        elif status == "running":
            if _run_is_dead(rec):
                if _try_resume_from_checkpoint(rec):
                    closed += 1
                elif rec.get("heartbeat_at"):
                    _fail_run(rec, (
                        "Run stopped reporting a heartbeat "
                        f"(quiet for more than {int(RUN_HEARTBEAT_STALE_SECONDS)}s): "
                        "its process is gone without finalizing."
                    ))
                    closed += 1
                elif rec.get("container_name"):
                    _fail_run(rec, "Run container exited without finalizing (crash or external kill).")
                    closed += 1
                else:
                    _fail_run(rec, "Run process died without finalizing (crash or external kill).")
                    closed += 1

        elif status == "queued":
            closed += _check_queued_run(rec)

    closed += _sweep_queue()
    _sweep_containers()
    closed += _sweep_flow_runs()
    closed += _sweep_loop_runs()
    closed += _sweep_instances()
    return closed


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


def _sweep_flow_runs() -> int:
    """Resume flow runs whose process died with a checkpoint; fail the rest."""
    try:
        from flow import run_store
    except ImportError:
        return 0

    handled = 0
    for rec in run_store.load_flow_runs():
        if str(rec.get("status") or "") != "running":
            continue
        if not _flow_run_is_dead(rec):
            continue

        flow_run_id = str(rec.get("flow_run_id") or "")
        attempts = int(rec.get("resume_attempts") or 0)
        checkpoint = rec.get("checkpoint") or {}
        if checkpoint and attempts < MAX_AUTO_RESUMES:
            try:
                from flow.launcher import resume_flow_run
                resume_flow_run(flow_run_id, auto=True)
                log.warning(
                    "watchdog resumed flow run %s from its checkpoint (attempt %d)",
                    flow_run_id[:8], attempts + 1,
                )
                handled += 1
                continue
            except Exception:
                log.exception("watchdog could not resume flow run %s", flow_run_id[:8])

        error = (
            "Flow run process stopped without finalizing"
            + (f" and could not be resumed after {attempts} attempt(s)."
               if checkpoint else " and had no checkpoint to resume from.")
        )
        try:
            run_store.close_flow_run(flow_run_id, status="failed", exit_code=1, error=error)
            from flow.launcher import _set_flow_running
            _set_flow_running(str(rec.get("flow_id") or ""), False)
            task_id = str(rec.get("task_id") or "")
            if task_id:
                from managers.run_manager import finalize_flow_task
                finalize_flow_task(task_id, "failed", 1, error=error)
            log.warning("watchdog failed flow run %s: %s", flow_run_id[:8], error)
            handled += 1
        except Exception:
            log.exception("watchdog could not close flow run %s", flow_run_id[:8])
    return handled


def _sweep_loop_runs() -> int:
    """Resume loop runs whose backend process died mid-iteration.

    A loop lives in the backend process, so a restart ends every loop that was
    running. Each completed iteration is a whole flow's worth of work, which is
    exactly why the run carries a position: the resume picks up at the iteration
    after the last one that finished, instead of paying for all of them again.
    """
    try:
        from loops import store as loop_store
    except ImportError:
        return 0

    handled = 0
    for run in loop_store.list_runs(limit=200):
        if run.status != "running":
            continue
        position = dict(run.position or {})
        heartbeat = str(position.get("heartbeat_at") or "")
        age = _age_seconds(heartbeat) if heartbeat else None
        if age is None or age <= LOOP_HEARTBEAT_STALE_SECONDS:
            continue

        attempts = int(position.get("resume_attempts") or 0)
        if position.get("iterations_done") and attempts < MAX_AUTO_RESUMES:
            try:
                import threading
                from loops.runner import resume_loop_run
                threading.Thread(
                    target=resume_loop_run, args=(run.loop_run_id,),
                    kwargs={"auto": True}, daemon=True,
                    name=f"loop-resume-{run.loop_run_id}",
                ).start()
                log.warning(
                    "watchdog resumed loop run %s from iteration %s (attempt %d)",
                    run.loop_run_id[:8], position.get("iterations_done"), attempts + 1,
                )
                handled += 1
                continue
            except Exception:
                log.exception("watchdog could not resume loop run %s", run.loop_run_id[:8])

        run.status = "failed"
        run.stop_reason = "error"
        run.error = (
            "The loop stopped without finishing (the backend process ended) and "
            + ("could not be resumed." if position.get("iterations_done")
               else "had no position to resume from.")
        )
        from loops.models import utc_iso
        run.finished_at = utc_iso()
        try:
            loop_store.save_run(run)
            log.warning("watchdog failed loop run %s", run.loop_run_id[:8])
            handled += 1
        except Exception:
            log.exception("watchdog could not close loop run %s", run.loop_run_id[:8])
    return handled


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
                if self._leader:
                    closed = await asyncio.to_thread(sweep_once)
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
