"""
Loop launches as a process: the database half, and, through the shared
envelope every entity kind spawns through (runtime/entity_launch.py), the
process half.

Before this module a loop run was a daemon thread of the API process
(``dashboard/backend/routes/loops.py``'s old ``start_run``): it died with
every restart of the backend, it could not be handed to a worker, and it
competed with request handling for the process's own resources. A loop is
the longest-running thing the hub has (a whole flow, repeated), which is the
worst possible candidate for living inside the web server.

Now :func:`start_loop_run` prepares the record, the task and the log path,
then hands a plain JSON spec to :func:`runtime.entity_launch.dispatch`, which
either spawns ``runtime/loop_run.py`` right here (the default role) or queues
the spec for a worker to spawn on its own host (the ``api`` role;
docs/workers.md). ``loops.runner.run_loop`` still does the actual iterations,
in that process, given the pre-created record and, on a resume, its position.
A loop always runs as a local subprocess, never in a container: its
iterations run the flow engine in-process, so the loop process is the
orchestrator of the agent runs, not their sandbox, the same as a flow.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT
from loops.models import LoopRun
from loops.runner import LoopResumeError

log = logging.getLogger(__name__)

#: What a loop launch is called on the queue and in runtime.entity_launch's
#: ENTRYPOINTS / LAUNCHERS tables.
QUEUE_KIND = "loop"


def _log_path(loop_run_id: str) -> str:
    log_dir = AGENTS_HUB_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / f"loop_run_{loop_run_id}.log")


def start_loop_run(
    loop_id: str,
    goal: str = "",
    *,
    workspace: Optional[str] = None,
    task_id: Optional[str] = None,
    seed: Optional[Dict[str, Any]] = None,
    parent_run_id: Optional[str] = None,
) -> LoopRun:
    """The database half of launching a loop run.

    Validates the loop and its flow the way ``loops.runner.run_loop`` does,
    resolves the workspace, task and session the way
    ``loops.runner._prepare_context`` does (which also records the loop as the
    task's executor), creates the ``LoopRun`` record as ``pending`` with its
    log path and the seed state, then builds the launch spec and hands it to
    :func:`runtime.entity_launch.dispatch`. Returns the record as stored
    (``pending``, or already ``running`` when this process spawned the child
    synchronously).
    """
    from runtime import entity_launch
    from loops import store
    from loops.runner import _prepare_context

    loop = store.get_loop(loop_id)
    if not loop:
        raise ValueError(f"Loop not found: {loop_id}")
    from flow import store as flow_store
    flow = flow_store.get_flow(loop.flow_id)
    if not flow:
        raise ValueError(f"Loop '{loop.name or loop_id}' references a missing flow: {loop.flow_id}")

    goal = (goal or "").strip() or (loop.description or "").strip() or (flow.get("description") or "")
    ws_name, ws_path, task_id, session_id = _prepare_context(loop, workspace, task_id, goal)

    run = LoopRun(
        loop_id=loop_id, workspace=ws_name, goal=goal, task_id=task_id,
        session_id=session_id, parent_run_id=parent_run_id, status="pending",
        started_at="",
    )
    run.host = None
    run.heartbeat_at = None
    store.save_run(run)
    log_file = _log_path(run.loop_run_id)
    # The seed rides on the record's document: the subprocess reads
    # everything it needs back from the record, so the spec stays small.
    from common import entity_runs
    entity_runs.update(run.loop_run_id, {"log_file": log_file, "seed": dict(seed or {})},
                       notify=False)
    # Point the task at this run (the executor itself was recorded by
    # _prepare_context before the run id existed).
    try:
        from tasks import service as _ts
        _ts.assign_executor(
            task_id, {"kind": "loop", "id": loop_id},
            {"loop_id": loop_id, "flow_id": loop.flow_id, "workspace": ws_name},
            run_id=run.loop_run_id,
        )
    except Exception:  # noqa: BLE001 - the run still launches; the task page just shows no run id yet
        log.debug("assigning loop run to task failed", exc_info=True)

    spec = {
        "kind": QUEUE_KIND, "run_id": run.loop_run_id, "entity_id": loop_id,
        "entrypoint": "loop_run", "cli_args": ["--run-id", run.loop_run_id],
        "workspace": ws_name, "ws_path": ws_path,
        "task_id": task_id, "session_id": session_id,
        "log_file": log_file, "header": "Loop run started", "mode": "w",
        "execution_mode": "local", "resume": False,
    }
    entity_launch.dispatch(spec, launch_prepared)
    return store.get_run(run.loop_run_id) or run


def resume_loop_run(loop_run_id: str, *, auto: bool = False) -> LoopRun:
    """Relaunch a stopped or failed loop run under the same id, from its
    position (the iteration after the last one it finished).

    Refuses (``LoopResumeError``) a run that is unknown, still live, finished,
    or has no position to resume from, the same rules
    ``loops.runner.resume_loop_run`` applies. ``auto`` marks a resume the
    watchdog performed and counts against its cap. The status moves back to
    ``running`` only once a process exists (``launch_prepared``), so a launch
    waiting in the queue reads honestly as ``stopped`` or ``failed`` until
    then.
    """
    from common import entity_runs
    from runtime import entity_launch
    from loops import store

    run = store.get_run(loop_run_id)
    if run is None:
        raise LoopResumeError(f"Loop run not found: {loop_run_id}")
    if run.status not in ("stopped", "failed"):
        raise LoopResumeError(
            f"Loop run {loop_run_id} is {run.status}; only a stopped or failed run can be resumed")
    if not (run.position or {}).get("iterations_done"):
        raise LoopResumeError(f"Loop run {loop_run_id} has no position to resume from")
    loop = store.get_loop(run.loop_id)
    if loop is None:
        raise LoopResumeError(f"Loop not found: {run.loop_id}")

    rec = store.RUNS.read(loop_run_id) or {}
    attempts = int(run.resume_attempts or 0) + (1 if auto else 0)
    log_file = rec.get("log_file") or _log_path(loop_run_id)
    entity_runs.update(loop_run_id, {"resume_attempts": attempts, "log_file": log_file},
                       notify=False)
    if auto:
        position = dict(run.position or {})
        position["resume_attempts"] = attempts
        store.save_position(loop_run_id, position)

    from workspace import create_workspace_folder, resolve_task_workspace, as_param_dict
    ws_name = run.workspace or loop.workspace
    ws_name = (create_workspace_folder(ws_name).name if ws_name
               else create_workspace_folder().name)
    ws_path = ws_name
    try:
        from tasks import service as _ts
        task = _ts.get_task(run.task_id) if run.task_id else None
        if task is not None:
            _, ws_path = resolve_task_workspace(task, as_param_dict({"workspace": ws_name}))
    except Exception:  # noqa: BLE001 - the workspace root is a fine fallback for the cwd
        log.debug("resolving task workspace failed", exc_info=True)

    spec = {
        "kind": QUEUE_KIND, "run_id": loop_run_id, "entity_id": run.loop_id,
        "entrypoint": "loop_run", "cli_args": ["--run-id", loop_run_id, "--resume"],
        "workspace": ws_name, "ws_path": str(ws_path),
        "task_id": run.task_id, "session_id": run.session_id,
        "log_file": log_file, "header": "Loop run resumed", "mode": "a",
        "execution_mode": "local", "resume": True,
    }
    entity_launch.dispatch(spec, launch_prepared)
    return store.get_run(loop_run_id) or run


def launch_prepared(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The process half of a loop launch: spawn ``runtime/loop_run.py`` on
    this host from a spec :func:`start_loop_run` or :func:`resume_loop_run`
    prepared. The shared envelope records the pid and the host and moves the
    record to ``running``; the flow's coarse running marker is flipped by the
    runner itself once the first iteration starts."""
    from runtime.entity_launch import launch_prepared as _launch
    spec.setdefault("execution_mode", "local")
    return _launch(spec)


def stop_loop_run(loop_run_id: str) -> bool:
    """Ask a loop run to stop, wherever it lives: the durable ``stopping``
    status is read by the runner between nodes and between iterations, in
    this process or in any other. A finished or unknown run changes nothing."""
    from loops import store
    return store.request_stop(loop_run_id)


__all__ = ["LoopResumeError", "QUEUE_KIND", "start_loop_run", "resume_loop_run",
           "launch_prepared", "stop_loop_run"]
