"""
Team launches as a process: the database half, and — through the shared
envelope every entity kind spawns through (runtime/entity_launch.py) — the
process half.

Before this module, a team run was a daemon thread of the API process
(``dashboard/backend/routes/teams.py``'s old ``start_run``): no sandbox, no
lease, no heartbeat of its own, and gone the moment the server restarted.
This module gives a team run the same shape a flow run has had since stage 2
of the scaling plan (flow/launcher.py, the reference this mirrors): a
:func:`start_team_run` prepares the record, the task and the log path, then
hands a plain JSON spec to :func:`runtime.entity_launch.dispatch`, which
either spawns ``runtime/team_run.py`` right here (the default role) or queues
the spec for a worker to spawn on its own host (the ``api`` role;
docs/workers.md). ``teams.runner.run_team`` still does the actual round loop
— in this process when it minted its own record (chat, a tool call), or in
the subprocess the spec above just started, given the pre-created record and,
on a resume, the checkpoint to pick up from.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT
from teams.models import TeamRun


class TeamResumeError(Exception):
    """A team run cannot be resumed: unknown, already live, or nothing to
    resume from."""


#: What a team launch is called on the queue and in runtime.entity_launch's
#: ENTRYPOINTS / LAUNCHERS tables (runtime/entity_launch.py).
QUEUE_KIND = "team"


def _log_path(team_run_id: str) -> str:
    log_dir = AGENTS_HUB_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / f"team_run_{team_run_id}.log")


def start_team_run(
    team_id: str,
    goal: str,
    *,
    workspace: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
) -> TeamRun:
    """The database half of launching a team run.

    Validates the team the same way ``teams.runner.run_team``'s own
    mint-a-record path does (``teams.runner._validate_for_run`` — one rule,
    shared, so a launch fails identically whichever door it came through),
    resolves the workspace/task/session the same way
    ``teams.runner._prepare_context`` does, creates the ``TeamRun`` record as
    ``pending`` and claims the task, so the caller sees the assignment before
    the process has even started. Then it builds the launch spec and hands it
    to :func:`runtime.entity_launch.dispatch`. Returns the ``TeamRun`` as
    written (``pending``, or already ``running`` if this process spawned the
    subprocess synchronously and it beat its first heartbeat before this
    returned — either is fine, the caller reads the live record to know).
    """
    from runtime import entity_launch
    from teams import store
    from teams.runner import _claim_task, _prepare_context, _validate_for_run

    team = store.get_team(team_id)
    if not team:
        raise ValueError(f"Team not found: {team_id}")
    _validate_for_run(team)

    goal = (goal or "").strip() or (team.description or "").strip()

    run = TeamRun(team_id=team_id, mode=team.mode, goal=goal,
                  conversation_id=conversation_id, parent_run_id=parent_run_id)

    ws_name, ws_path, task_id, session_id = _prepare_context(
        team, workspace, task_id, session_id, conversation_id, goal,
    )
    run.workspace, run.task_id, run.session_id = ws_name, task_id, session_id
    run.log_file = _log_path(run.team_run_id)

    store.save_run(run)
    # The task is claimed before the process exists, exactly as the old
    # in-process path did: the user picked this team and pressed run, so
    # there is nobody left to approve the assignment (see _claim_task's own
    # docstring for why an unclaimed task would sit behind an approval step).
    _claim_task(team, run)

    execution_mode = entity_launch.execution_mode_for(ws_name)
    spec = {
        "kind": QUEUE_KIND, "run_id": run.team_run_id, "entity_id": team_id,
        "entrypoint": "team_run", "cli_args": ["--run-id", run.team_run_id],
        "workspace": ws_name, "ws_path": ws_path,
        "task_id": task_id, "session_id": session_id,
        "log_file": run.log_file, "header": "Team run started", "mode": "w",
        "execution_mode": execution_mode, "resume": False,
    }
    entity_launch.dispatch(spec, launch_prepared)
    return store.get_run(run.team_run_id) or run


def resume_team_run(team_run_id: str, *, auto: bool = False) -> TeamRun:
    """Relaunch a stopped or failed run under the same id, from its checkpoint.

    Refuses (``TeamResumeError``) a run that is unknown, still live, finished
    successfully, or has no checkpoint — there is nothing to pick back up
    from. The status itself is left alone here: ``launch_prepared`` (via
    ``runtime.entity_launch.launch_prepared`` -> ``entity_runs.mark_running``)
    is what moves it back to ``running``, once a process actually exists for
    it — in the ``api`` role that can be a while after this call returns, and
    the record should say so honestly in the meantime rather than claim
    ``running`` for a launch still sitting in the queue.
    """
    from common import entity_runs
    from runtime import entity_launch
    from teams import store
    from teams.runner import _resolve_run_workspace

    rec = store.get_run(team_run_id)
    if rec is None:
        raise TeamResumeError(f"Team run not found: {team_run_id}")
    if rec.status not in ("stopped", "failed"):
        raise TeamResumeError(
            f"Team run {team_run_id} is {rec.status}; only a stopped or "
            "failed run can be resumed"
        )
    checkpoint = entity_runs.load_checkpoint(team_run_id)
    if not checkpoint:
        raise TeamResumeError(
            f"Team run {team_run_id} has no checkpoint to resume from"
        )

    team = store.get_team(rec.team_id)
    if not team:
        raise TeamResumeError(f"Team not found: {rec.team_id}")

    attempts = int(rec.resume_attempts or 0) + (1 if auto else 0)
    log_file = rec.log_file or _log_path(team_run_id)
    entity_runs.update(team_run_id, {"resume_attempts": attempts, "log_file": log_file})

    ws_name, ws_path = _resolve_run_workspace(rec)
    execution_mode = entity_launch.execution_mode_for(ws_name)
    spec = {
        "kind": QUEUE_KIND, "run_id": team_run_id, "entity_id": rec.team_id,
        "entrypoint": "team_run",
        "cli_args": ["--run-id", team_run_id, "--resume"],
        "workspace": ws_name, "ws_path": ws_path,
        "task_id": rec.task_id, "session_id": rec.session_id,
        "log_file": log_file, "header": "Team run resumed", "mode": "a",
        "execution_mode": execution_mode, "resume": True,
    }
    entity_launch.dispatch(spec, launch_prepared)
    return store.get_run(team_run_id) or rec


def launch_prepared(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The process half of a team launch: spawn ``runtime/team_run.py`` on
    this host (or in a container) from a spec :func:`start_team_run` or
    :func:`resume_team_run` prepared. Wraps the shared envelope
    (runtime.entity_launch.launch_prepared), which records the pid/container
    and the host and moves the record to ``running``; a team has no
    after-spawn bookkeeping of its own (a flow flips a "running" marker on
    its definition, a team has nothing equivalent — the record itself is the
    only place a team run's liveness is read from).
    """
    from runtime.entity_launch import launch_prepared as _launch
    return _launch(spec)


def stop_team_run(team_run_id: str) -> bool:
    """Stop a team run now, wherever it lives.

    Two halves, always both attempted: the durable status
    (``entity_runs``, via ``teams.store.request_stop``) is what a reloaded
    page, a worker, or a process on another host reads; ``teams.control`` is
    the immediate half, for a run this process is actually carrying (in
    process, or via the subprocess's own ``EntityHeartbeat`` -> ``on_stop``
    hook — see runtime/team_run.py). A run already finished, or unknown,
    changes nothing and this returns False.
    """
    from teams import control, store

    if not store.request_stop(team_run_id):
        return False
    control.request_stop(team_run_id)
    try:
        from teams.runner import _publish
        _publish(team_run_id, {"type": "stopping", "team_run_id": team_run_id,
                               "status": "stopping"})
    except Exception:  # noqa: BLE001 - the publish is a live-page convenience, not the stop itself
        pass
    return True


__all__ = [
    "TeamResumeError", "start_team_run", "resume_team_run", "launch_prepared",
    "stop_team_run",
]
