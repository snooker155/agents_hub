"""
Scenario launches: the database half of the shared launch envelope.

A simulation used to run on a daemon thread of whichever API process
received the request (``playground.runner.run_simulation``, started by
``dashboard/backend/routes/playground.py`` or ``tools/entity_runs.py``): no
lease, no heartbeat of its own, no place in the run queue, and gone for good
if that process restarted mid-run. This module is the other half — the
database preparation and the process spawn, exactly the shape a flow run has
had since stage 2 of the scaling plan (``flow/launcher.py``): a *database
half* validates and records the run before anything is spawned, and a
*process half* turns the record into a process, locally or on a worker in
the ``api`` role (``runtime/entity_launch.py``, docs/workers.md).

``runtime/scenario_run.py`` is the entrypoint this launches: it loads the
run (and, on a resume, its checkpoint), beats its own heartbeat, and calls
``playground.runner.run_simulation(run=..., checkpoint=...)`` to execute it.

The old thread-based path is not removed — ``playground.runner.run_simulation``
with no ``run`` still works, for the tools and for tests that want the loop
without a subprocess — this module is what the dashboard route and every
other caller that wants a real process now goes through.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT
from playground import runner, store
from playground.models import SimRun, utc_iso

log = logging.getLogger(__name__)


def _log_dir() -> Path:
    log_dir = AGENTS_HUB_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def start_scenario_run(
    scenario_id: str,
    *,
    workspace: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
) -> SimRun:
    """Validate the scenario, write its run record and launch the process.

    Raises ``ValueError`` for the same reasons ``run_simulation``'s own head
    always has (no scenario, no roles, too many roles, a duplicate display
    name, an unknown environment) — see
    ``playground.runner.validate_scenario_for_run`` — so a scenario that
    cannot run fails here, before a row exists for it, rather than inside the
    subprocess a moment later.
    """
    scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise ValueError(f"Scenario not found: {scenario_id}")
    runner.validate_scenario_for_run(scenario)

    ws = workspace or scenario.workspace
    # A caller-supplied task_id wins; otherwise a scenario built for one task
    # runs against it by default, the same way a loop's own task_id does.
    task_id = task_id or scenario.task_id
    run = SimRun(
        scenario_id=scenario_id, workspace=ws, environment=scenario.environment,
        activation=scenario.activation,
        # Frozen at launch, same as the thread-based path always did: the
        # scenario is free to be edited while this run — or a later one — is
        # going, and this run must keep running under the settings it started
        # with.
        config=scenario.to_dict(),
        task_id=task_id, session_id=session_id, parent_run_id=parent_run_id,
        created_at=utc_iso(),
    )
    run.log_file = str(_log_dir() / f"scenario_run_{run.sim_run_id}.log")
    store.save_sim_run(run)

    if task_id:
        # Point the task at this run, the way loops.launcher.start_loop_run
        # does. Best effort: the run still launches even when the task
        # cannot be updated, and the task page simply shows no run id yet.
        try:
            from tasks import service as _ts
            _ts.assign_executor(
                task_id, {"kind": "scenario", "id": scenario_id},
                {"scenario_id": scenario_id, "workspace": ws},
                run_id=run.sim_run_id,
            )
        except Exception:  # noqa: BLE001 - the task link is best effort, the scenario run still starts
            log.debug("task executor assignment failed", exc_info=True)

    from runtime.entity_launch import dispatch, execution_mode_for

    spec = {
        "kind": "scenario", "run_id": run.sim_run_id, "entity_id": scenario_id,
        "entrypoint": "scenario_run", "cli_args": ["--run-id", run.sim_run_id],
        "workspace": ws, "ws_path": str(PROJECT_ROOT),
        "task_id": task_id, "session_id": session_id,
        "log_file": run.log_file, "header": "Scenario run started", "mode": "w",
        "execution_mode": execution_mode_for(ws), "resume": False,
    }
    dispatch(spec, launch_prepared)
    return run


class ScenarioResumeError(Exception):
    """A scenario run cannot be resumed (unknown run, already completed, or
    no checkpoint to resume from)."""


def resume_scenario_run(sim_run_id: str, *, auto: bool = False) -> SimRun:
    """Relaunch a scenario run under the same id, from its checkpoint.

    Mirrors ``flow.launcher.resume_flow_run``: the record moves straight to
    ``running`` (legal from ``stopped``/``failed`` per
    ``common/run_status.py``'s transition table, the same edge a resumed
    flow or agent run takes) before the process exists, so a reload of the
    page shows the resume immediately rather than a beat later when the
    subprocess's own heartbeat first fires. ``auto`` marks a resume the
    watchdog performed rather than a person, and is what counts against
    ``resume_attempts``.
    """
    run = store.get_sim_run(sim_run_id)
    if run is None:
        raise ScenarioResumeError(f"Scenario run not found: {sim_run_id}")
    if run.status == "completed":
        raise ScenarioResumeError(f"Scenario run {sim_run_id} has already completed")

    from common import entity_runs

    checkpoint = entity_runs.load_checkpoint(sim_run_id)
    if not checkpoint:
        raise ScenarioResumeError(
            f"Scenario run {sim_run_id} has no checkpoint to resume from"
        )

    attempts = run.resume_attempts + (1 if auto else 0)
    now = entity_runs.utc_now_iso()
    store.RUNS.update(sim_run_id, {
        "status": "running", "resume_attempts": attempts, "heartbeat_at": now,
        "finished_at": None, "exit_code": None, "error": None,
    })

    log_file = run.log_file or str(_log_dir() / f"scenario_run_{sim_run_id}.log")
    from runtime.entity_launch import dispatch, execution_mode_for

    spec = {
        "kind": "scenario", "run_id": sim_run_id, "entity_id": run.scenario_id,
        "entrypoint": "scenario_run",
        "cli_args": ["--run-id", sim_run_id, "--resume"],
        "workspace": run.workspace, "ws_path": str(PROJECT_ROOT),
        "task_id": run.task_id, "session_id": run.session_id,
        "log_file": log_file, "header": "Scenario run resumed", "mode": "a",
        "execution_mode": execution_mode_for(run.workspace), "resume": True,
    }
    dispatch(spec, launch_prepared)
    resumed = store.get_sim_run(sim_run_id)
    return resumed if resumed is not None else run


def launch_prepared(spec: Dict[str, Any]) -> None:
    """The process half of a scenario launch: spawn
    ``runtime/scenario_run.py`` on this host from a spec :func:`start_scenario_run`
    or :func:`resume_scenario_run` prepared. Thin on purpose — a scenario has
    no coarse "running" marker to flip and no task to hand back to, unlike a
    flow, so there is nothing to do here beyond what the shared envelope
    (``runtime/entity_launch.py``) already does: spawn the process (or a
    container) and record where it lives."""
    from runtime.entity_launch import launch_prepared as _launch

    spec.setdefault("execution_mode", "local")
    _launch(spec)


def stop_scenario_run(sim_run_id: str) -> bool:
    """Stop a scenario run now, wherever it is executing.

    Delegates to ``playground.runner.stop_simulation``, which already does
    exactly this (the durable ``stopping`` status plus the in-memory event
    for a run this process happens to be executing) — kept there rather than
    duplicated here so the tools and the tests that call it directly keep
    working unchanged.
    """
    return runner.stop_simulation(sim_run_id)


def trigger_scenario_run(sim_run_id: str, agent: str, text: str,
                         sender: str = "(external)") -> bool:
    """Poke one agent in a running scenario, wherever it is executing.

    Delegates to ``playground.runner.trigger_agent``, which pushes the
    message durably (reaching a run in another process) and, when this
    process happens to be the one executing it, wakes an idle wait
    immediately too.
    """
    return runner.trigger_agent(sim_run_id, agent, text, sender)


__all__ = [
    "start_scenario_run", "resume_scenario_run", "launch_prepared",
    "stop_scenario_run", "trigger_scenario_run", "ScenarioResumeError",
]
