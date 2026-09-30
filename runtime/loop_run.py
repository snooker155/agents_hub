"""
Loop subprocess entry point.

Runs one loop to convergence (or until stopped, or the process dies) through
``loops.runner.run_loop``, given the record ``loops.launcher.start_loop_run``
(or ``resume_loop_run``) already created. The record, the task claim and the
log path are the launcher's database half; this process supplies what every
entity kind's subprocess supplies (runtime/agent_run.py, runtime/flow_run.py,
runtime/team_run.py): a heartbeat, so the watchdog and a stop from another
host both work, and, with ``--resume``, the position the iteration loop picks
back up from.

A loop's own stop check is durable already (``loops.store.stop_requested``,
read between nodes and between iterations), so the heartbeat's stop hook has
nothing to deliver in memory; the beat exists so a run on a worker is judged
by its sign of life and not by a pid the backend cannot see.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.logging_config import marker_logger

# ``[loop_start]`` / ``[loop_resume]`` / ``[loop_done]`` / ``[loop_error]``
# are the markers a reader greps out of the run's log, the same way
# flow_run.py's ``[flow_*]`` markers are.
log = marker_logger(__name__)


def _tee_stdout_to_log(log_file: str) -> None:
    """Mirror stdout/stderr into the run's log file (Docker mode only; a local
    subprocess has its output piped into the log by the parent's Popen)."""
    try:
        from common.utils import Tee
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        lf = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the process
        sys.stdout = Tee(sys.__stdout__, lf)
        sys.stderr = Tee(sys.__stderr__, lf)
    except Exception:  # noqa: BLE001 - losing the tee is not worth failing the run over
        log.debug("could not tee stdout/stderr to %s", log_file, exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a loop to convergence")
    parser.add_argument("--run-id", required=True,
                        help="loop_run_id; loops.launcher already created the record")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from the run's stored position")
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
    from loops import store as loop_store
    from loops.runner import resume_loop_run, run_loop

    run = loop_store.get_run(run_id)
    if run is None:
        log.error(f"[loop_error] loop run not found: {run_id}")
        sys.exit(1)
    rec = loop_store.RUNS.read(run_id) or {}

    heartbeat = EntityHeartbeat(run_id)
    heartbeat.start()

    exit_code = 1
    try:
        if args.resume:
            log.info(f"[loop_resume] resuming {run_id} from iteration "
                     f"{(run.position or {}).get('iterations_done', 0)}")
            finished = resume_loop_run(run_id, own_process=False)
        else:
            log.info(f"[loop_start] loop_run={run_id} loop_id={run.loop_id}")
            seed = rec.get("seed") if isinstance(rec.get("seed"), dict) else None
            finished = run_loop(run.loop_id, goal=run.goal, workspace=run.workspace,
                                task_id=run.task_id, seed=seed, run=run, own_process=False)
        exit_code = 0 if finished.status == "completed" else 1
        log.info(f"[loop_done] loop_run={run_id} status={finished.status} "
                 f"iterations={finished.iterations_done}")
    except Exception as e:  # noqa: BLE001 - the process's own crash path: closed and logged, never silently lost
        log.exception("loop run crashed")
        try:
            entity_runs.close(run_id, status="failed", exit_code=1,
                              error=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001 - the record may already be closed; nothing more to do
            log.debug("could not close run %s after a crash", run_id, exc_info=True)
    finally:
        heartbeat.stop()
        try:
            from common import blobs
            log_file = os.environ.get("AGENT_LOG_FILE") or rec.get("log_file")
            if log_file:
                blobs.mirror(blobs.rel(log_file))
        except Exception:  # noqa: BLE001 - never turn a finished run into a reported failure
            log.debug("could not mirror the log for run %s", run_id, exc_info=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
