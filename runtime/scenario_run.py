"""
Scenario subprocess entry point.

The process half of a scenario launch (``playground/launcher.py``): loads the
run record ``start_scenario_run`` (or ``resume_scenario_run``) already wrote,
beats a heartbeat for as long as the simulation runs, and hands the whole
thing to ``playground.runner.run_simulation`` — the tick loop itself is
unchanged, this is only what stands the process up around it. Mirrors
``runtime/flow_run.py``: a database half prepares the record and a spec, the
shared envelope (``runtime/entity_launch.py``) spawns this script, and this
script's job is preflight plus driving the loop.

``--resume`` replays the run's checkpoint (``common/entity_runs.py``,
written by the tick loop after every tick): the environment is restored to
where it stood, the ticks it had already written are not redone, and the
loop continues from the tick after the checkpoint's.

Exit code 0 means the run finished (``status == "completed"``); anything
else (stopped, failed, or the run row was gone) is 1, the same convention
``runtime/flow_run.py`` uses for "did not finish clean".
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Spawned as ``python runtime/scenario_run.py`` (or ``-m runtime.scenario_run``
# in a container), which puts ``runtime/`` — not the repo root — on
# sys.path[0]. Put the repo root on the path first, mirroring
# runtime/flow_run.py and runtime/agent_run.py, so first-party packages
# resolve whether this runs as a script or as ``runtime.scenario_run``.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.logging_config import configure_logging, marker_logger

# The marker lines below (``[scenario_start]``, ``[scenario_resume]``,
# ``[scenario_done]``, ``[scenario_error]``) are what the log tail and any
# future grep-based tooling reads back out of the run's log file, so they go
# through a logger configured to emit the message only, on stdout, at INFO
# regardless of ORCH_LOG_LEVEL — the same contract runtime/flow_run.py and
# runtime/agent_run.py hold.
log = marker_logger(__name__)


def _tee_stdout_to_log(log_path: str) -> None:
    """Mirror stdout/stderr into the run's log file (Docker execution only —
    the log lives inside the container's own filesystem there, so the parent
    process that wrote the header cannot tee it from outside). Appends: the
    header line is already there. Mirrors runtime/agent_run.py's
    ``_setup_docker_log_tee``."""
    try:
        from common.utils import Tee
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        lf = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = Tee(sys.__stdout__, lf)
        sys.stderr = Tee(sys.__stderr__, lf)
    except Exception:  # noqa: BLE001 - logging must never keep the run from starting
        log.debug("could not tee stdout to %s", log_path, exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a playground scenario as its own process")
    parser.add_argument("--run-id", required=True, help="Scenario run id (sim_run_id)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from this run's checkpoint instead of starting fresh")
    parser.add_argument("--write-stdout-to-log", action="store_true",
                        help="Mirror stdout/stderr into AGENT_LOG_FILE (Docker runs)")
    args = parser.parse_args()

    if args.write_stdout_to_log:
        log_file = os.environ.get("AGENT_LOG_FILE")
        if log_file:
            _tee_stdout_to_log(log_file)

    # No load_dotenv() here: like the other subprocess entrypoints, this
    # process inherits the launcher's already-populated environment.
    configure_logging(os.environ.get("AGENT_WORKSPACE"))

    from playground import control, store
    from playground.runner import run_simulation
    from runtime.entity_heartbeat import EntityHeartbeat

    run_id = args.run_id
    run = store.get_sim_run(run_id)
    if run is None:
        log.error(f"[scenario_error] run not found: {run_id}")
        sys.exit(1)

    checkpoint = None
    if args.resume:
        from common import entity_runs
        checkpoint = entity_runs.load_checkpoint(run_id)
        if checkpoint:
            log.info(f"[scenario_resume] resuming {run_id} from tick "
                     f"{checkpoint.get('tick')}")
        else:
            log.info(f"[scenario_resume] {run_id} has no checkpoint — starting from tick 0")

    log.info(f"[scenario_start] run_id={run_id} scenario_id={run.scenario_id} "
             f"resume={bool(args.resume)}")

    # Registered before the heartbeat starts: its ``on_stop`` reads
    # ``playground.control``, and a stop that lands on the very first beat
    # must find a registry entry already there rather than a no-op (the
    # in-flight decisions would still be caught by the durable
    # ``store.stop_requested`` checks the tick loop itself makes, but there is
    # no reason to leave that gap open when registering first closes it).
    control.register(run_id)
    heartbeat = EntityHeartbeat(run_id, on_stop=lambda: control.request_stop(run_id))
    heartbeat.start()

    finished = None
    try:
        finished = run_simulation(run.scenario_id, workspace=run.workspace,
                                  run=run, checkpoint=checkpoint)
    except Exception as e:  # noqa: BLE001 - a crash here still has to close the run and exit non-zero
        log.exception("scenario run failed")
        try:
            from common import entity_runs
            entity_runs.close(run_id, status="failed", exit_code=1,
                              error=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001 - best-effort; the process exits 1 regardless
            log.debug("could not close run %s after a crash", run_id, exc_info=True)
    finally:
        heartbeat.stop()
        # Best-effort: a replica or worker on another host can then serve this
        # run's finished log even though it never ran the process itself
        # (common/blobs.py, docs/storage.md).
        try:
            from common import blobs
            log_file = os.environ.get("AGENT_LOG_FILE")
            if log_file:
                blobs.mirror(blobs.rel(log_file))
        except Exception:  # noqa: BLE001 - mirroring the log must never fail the run
            log.debug("log mirror failed for %s", run_id, exc_info=True)

    if finished is None:
        sys.exit(1)
    log.info(f"[scenario_done] run_id={run_id} status={finished.status} "
             f"ticks_done={finished.ticks_done} stop_reason={finished.stop_reason}")
    sys.exit(0 if finished.status == "completed" else 1)


if __name__ == "__main__":
    main()
