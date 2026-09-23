"""
Team subprocess entry point.

Runs one team to completion (or until stopped, or the process dies) through
``teams.runner.run_team``, given the record ``teams.launcher.start_team_run``
(or ``resume_team_run``) already created — the record, the task claim and the
log path are the launcher's database half; this process supplies the other
half every entity kind's subprocess supplies (runtime/agent_run.py,
runtime/flow_run.py): a heartbeat, so the watchdog and a stop from another
host both work, and — with ``--resume`` — the checkpoint the round loop picks
back up from.

The round loop itself, the board, the modes: none of that lives here. This
file is bootstrap and bookkeeping around one call to ``run_team``, the same
division flow_run.py keeps between the DAG walk (flow.engine) and its own
subprocess plumbing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Spawned with ``runtime/`` (not the repo root) as sys.path[0]; put the repo
# root on the path before the first-party imports below (mirrors
# runtime/agent_run.py and runtime/flow_run.py).
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.logging_config import marker_logger

# ``[team_start]`` / ``[team_resume]`` / ``[team_done]`` / ``[team_error]`` are
# grepped back out of the run's log the same way flow_run.py's ``[flow_*]``
# markers are (dashboard routes, tests), so they go through a logger
# configured to emit the message only, on stdout, at INFO regardless of
# ORCH_LOG_LEVEL.
log = marker_logger(__name__)


def _tee_stdout_to_log(log_file: str) -> None:
    """Mirror stdout/stderr into the run's log file.

    Only needed in Docker execution mode (runtime.entity_launch appends
    ``--write-stdout-to-log`` to the *containerized* command only): a local
    subprocess already has its stdout/stderr piped straight into the log file
    by the parent's own ``Popen`` (runtime.entity_launch.spawn_local), the
    same way runtime/agent_run.py's ``_setup_docker_log_tee`` is only active
    for a Docker task run.
    """
    try:
        from common.utils import Tee
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        lf = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the process
        sys.stdout = Tee(sys.__stdout__, lf)
        sys.stderr = Tee(sys.__stderr__, lf)
    except Exception:  # noqa: BLE001 - losing the tee is not worth failing the run over
        log.debug("could not tee stdout/stderr to %s", log_file, exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a team to completion")
    parser.add_argument("--run-id", required=True,
                        help="team_run_id; teams.launcher already created the record")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from the run's stored checkpoint")
    parser.add_argument("--write-stdout-to-log", action="store_true",
                        help="Mirror stdout/stderr into the run's log file (Docker execution mode)")
    args = parser.parse_args()

    run_id = args.run_id

    if args.write_stdout_to_log:
        log_file = os.environ.get("AGENT_LOG_FILE")
        if log_file:
            _tee_stdout_to_log(log_file)

    from common.logging_config import configure_logging
    configure_logging(os.environ.get("AGENT_WORKSPACE"))

    from common import entity_runs
    from runtime.entity_heartbeat import EntityHeartbeat
    from teams import control
    from teams import store as team_store
    from teams.runner import run_team

    run = team_store.get_run(run_id)
    if run is None:
        log.error(f"[team_error] team run not found: {run_id}")
        sys.exit(1)

    checkpoint = None
    if args.resume:
        checkpoint = entity_runs.load_checkpoint(run_id)
        log.info(f"[team_resume] resuming {run_id} from round "
                 f"{(checkpoint or {}).get('round', 0)}")

    # Registered before the heartbeat starts: a stop that lands in the
    # sub-millisecond gap before run_team registers its own (idempotent) copy
    # would otherwise have nowhere to deliver on_stop, and would only be
    # caught later by the round loop's own durable-status check instead.
    control.register(run_id)
    heartbeat = EntityHeartbeat(run_id, on_stop=lambda: control.request_stop(run_id))
    heartbeat.start()

    log.info(f"[team_start] team_run={run_id} team_id={run.team_id} mode={run.mode}")

    exit_code = 1
    try:
        finished = run_team(run.team_id, run.goal, run=run, checkpoint=checkpoint)
        exit_code = 0 if finished.status == "completed" else 1
        log.info(f"[team_done] team_run={run_id} status={finished.status} "
                 f"rounds={finished.rounds_done}")
    except Exception as e:  # noqa: BLE001 - the process's own crash path: closed and logged, never silently lost
        log.exception("team run crashed")
        try:
            entity_runs.close(run_id, status="failed", exit_code=1,
                              error=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001 - the record may already be closed, or unreachable; nothing more to do
            log.debug("could not close run %s after a crash", run_id, exc_info=True)
    finally:
        heartbeat.stop()
        control.release(run_id)
        # Best-effort: a replica or worker on another host can then serve this
        # run's finished log even though it never ran the process itself
        # (common/blobs.py, docs/storage.md), the same as runtime/agent_run.py
        # does for a single agent's run.
        try:
            from common import blobs
            log_file = os.environ.get("AGENT_LOG_FILE") or run.log_file
            if log_file:
                blobs.mirror(blobs.rel(log_file))
        except Exception:  # noqa: BLE001 - never turn a finished run into a reported failure
            log.debug("could not mirror the log for run %s", run_id, exc_info=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
