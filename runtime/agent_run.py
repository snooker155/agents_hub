import argparse
import os
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import Optional
from uuid import uuid4

# This entrypoint is spawned as a subprocess with cwd set to the workspace
# directory, so the repo root is not on sys.path by default. Put it there before
# the first-party imports below (mirrors runtime/node_run.py and runtime/flow_run.py).
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.logging_config import marker_logger
from common.paths import AGENTS_HUB_ROOT
from common.state_transport import get_state_transport
from managers.run_manager import _utc_now_iso
from agents.agent_lifecycle import run_agent_lifecycle
from agents.callbacks import SessionPublishCallback as _SessionPublishCallback
from common.utils import Tee as _Tee  # Tee re-exported for back-compat (was defined here)
from tasks.context import build_task_instruction

# The marker lines below (``[heartbeat]``, ``[resume]``, ``Running agent with
# instruction:``, ``Agent output:``, ...) are what agents.agent_launcher pipes
# into the run's log file and what dashboard/backend/routes/sessions.py and
# several tests grep back out of it, so they go through a logger configured to
# emit the message only, on stdout, at INFO regardless of ORCH_LOG_LEVEL.
log = marker_logger(__name__)


def _setup_cli_log(run_id: str, workspace: str) -> str:
    """Create an agent_run_<id>.log file in the state run_logs folder and tee
    sys.stdout / sys.stderr to it so CLI runs are captured just like
    subprocess runs spawned by agent_launcher.  Returns the log file path."""
    log_dir = AGENTS_HUB_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"agent_run_{run_id}.log"
    lf = open(log_path, "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    try:
        lf.write(f"--- Run started at {_utc_now_iso()} ---\n\n")
    except Exception:
        pass
    sys.stdout = _Tee(sys.__stdout__, lf)
    sys.stderr = _Tee(sys.__stderr__, lf)
    return str(log_path)


def _setup_docker_log_tee(log_path: str) -> None:
    """Tee sys.stdout / sys.stderr into a Docker task run's log file.

    Mirrors ``_setup_cli_log`` above, except it appends instead of truncating:
    the launcher (``agents.agent_launcher._start_run_in_docker``) already wrote
    the run's header line to this file before the container started, and that
    header must survive. Active only with ``--write-stdout-to-log`` (passed for
    the inner command of a Docker task run, never the local subprocess path),
    so a detached container's run log carries full output the same way a local
    subprocess run's does from its Popen pipe, instead of staying just the
    header. See docs/containers.md, "Logs in Docker mode".
    """
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        lf = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = _Tee(sys.__stdout__, lf)
        sys.stderr = _Tee(sys.__stderr__, lf)
    except Exception:
        pass


# -------------------- Heartbeat --------------------

#: Seconds between heartbeats. The watchdog treats a run quiet for
#: RUN_HEARTBEAT_STALE_SECONDS (default 180) as dead, so this must be several
#: times shorter.
HEARTBEAT_SECONDS = float(os.environ.get("RUN_HEARTBEAT_SECONDS", "15"))


class _Heartbeat(threading.Thread):
    """Stamps the run's ``heartbeat_at`` every few seconds for as long as the
    process lives, through the same transport the run's other writes use.

    A stop requested from another host cannot signal this process, so it sets
    the record's status to ``stop`` instead; the beat reads the status back
    and delivers the signal locally, exactly what a same-host stop would have
    sent (``managers.runs.lifecycle._stop_run_record``).
    """

    def __init__(self, run_id: str, state) -> None:
        super().__init__(name=f"heartbeat-{run_id[:8]}", daemon=True)
        self.run_id = run_id
        self._state = state
        # Not ``_stop``: threading.Thread has a private ``_stop()`` method of
        # its own on Python 3.11 and 3.12, called from join(), and shadowing
        # it with an Event breaks the join.
        self._halt = threading.Event()
        self.beats = 0

    def run(self) -> None:
        while not self._halt.wait(HEARTBEAT_SECONDS):
            try:
                status = self._state.heartbeat(self.run_id)
            except Exception:
                continue
            self.beats += 1
            if status == "stop":
                log.info(f"[heartbeat] stop requested for run {self.run_id}; terminating")
                try:
                    os.kill(os.getpid(), signal.SIGTERM)
                except Exception:
                    os._exit(143)
                return

    def stop(self) -> None:
        self._halt.set()


# -------------------- Run lifecycle --------------------

def _register_run_start(run_id: str, agent_id: str, ws: str, task_id: Optional[str]) -> Optional[str]:
    """Create the run record at subprocess startup via run_manager.open_run."""
    try:
        # When invoked via CLI (not as a subprocess of agent_launcher), no log file
        # is set up externally — create one and tee stdout/stderr to it.
        if not os.environ.get("AGENT_LOG_FILE"):
            os.environ["AGENT_LOG_FILE"] = _setup_cli_log(run_id, ws)

        get_state_transport().open_run(
            run_id,
            agent_id,
            task_id=str(task_id) if task_id else None,
            session_id=os.environ.get("AGENT_SESSION_ID") or None,
            session_type="task",
            pid=os.getpid(),
            log_file=os.environ.get("AGENT_LOG_FILE") or None,
            execution_mode=os.environ.get("AGENT_EXECUTION_MODE", "local"),
            # Parent launchers (e.g. continuation) set AGENT_RUN_CHANNEL; the
            # default covers ordinary local subprocess runs.
            channel=os.environ.get("AGENT_RUN_CHANNEL", "local"),
            # The live copy this process *is*. The launcher pre-registered it and
            # passed the id down; a bare CLI run has none and stays unlinked.
            instance_id=os.environ.get("AGENT_INSTANCE_ID") or None,
            # Where the process runs, so a watchdog on another host knows not
            # to probe this pid, and the first beat, so a run is never judged
            # by a pid at all once it has started (managers/run_watchdog.py).
            host=socket.gethostname(),
            heartbeat_at=_utc_now_iso(),
        )
        return run_id
    except Exception:
        pass


def _update_run_lifecycle(run_id: str, task_id, result, agent_id: str = "", process: Optional[dict] = None) -> None:
    """Update run state, persist task result, and finalize task status.

    Every write below goes through the state transport (db, direct, by
    default; http for a run container whose state dir is read-only, see
    common/state_transport.py) instead of calling managers.run_manager /
    tasks.* directly, but each call is the same call at the same point in the
    same order as before that module existed.
    """
    try:
        state = get_state_transport()
        state.close_run_from_result(run_id, result, **({"process": process} if process else {}))

        # The agent paused via ask_user: persist the question as the task result so
        # it's visible (and picked up as prior context on resume), then park the
        # task in awaiting_input instead of resolving it. No finalize → any waiting
        # session continuation stays pending until the user answers.
        if getattr(result, "status", "") == "awaiting_input":
            if task_id:
                state.persist_task_result(task_id, run_id, result.agent_output or "", agent_id=agent_id)
                state.park_task_awaiting_input(run_id, getattr(result, "pending_question", None) or {}, agent_id=agent_id)
            return

        # The agent stopped on a tool call that needs the user's approval. Same
        # shape as the ask_user park above — persist what it said, park the task,
        # no finalize — but the payload is the call itself, so the user can see
        # exactly what would run before letting it (see agents/hooks.py).
        if getattr(result, "status", "") == "awaiting_approval":
            if task_id:
                state.persist_task_result(task_id, run_id, result.agent_output or "", agent_id=agent_id)
                state.park_task_awaiting_approval(
                    task_id,
                    getattr(result, "pending_approval", None) or {},
                    run_id=run_id, agent_id=agent_id,
                )
            return

        if result.ok and task_id:
            state.persist_task_result(task_id, run_id, result.agent_output or "", agent_id=agent_id)

        try:
            state.finalize_task_from_run(run_id, "completed" if result.ok else "failed", 0 if result.ok else 1)
        except Exception:
            pass
    except Exception:
        pass


# -------------------- Entry point --------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", help="Agent ID from definitions (e.g. swe_agent, researcher_agent)")
    ap.add_argument("action", nargs="?", help="Optional action/instruction")
    ap.add_argument("--desc", help="Initial description for the task")
    ap.add_argument("--task-id", help="Optional task ID to associate with")
    ap.add_argument("--run-id", help="Optional run ID to update lifecycle state for")
    ap.add_argument("--workspace", help="Path to workspace")
    ap.add_argument("-v", "--verbose", action="store_true")
    # Continuing a run the agent itself paused, rather than starting one. Only
    # an imported agent that declares runtime.resume_path can do this; for every
    # other agent the answer is folded into a fresh prompt instead, because they
    # keep nothing between runs for a resume to come back to.
    ap.add_argument("--resume-run", help="Run id the remote agent paused, to continue")
    ap.add_argument("--resume-value", help="The answer, JSON-encoded")
    ap.add_argument("--resume-key", help="The agent's own id for the question, when it gave one")
    ap.add_argument("--write-stdout-to-log", action="store_true",
                     help="Mirror stdout/stderr into AGENT_LOG_FILE (used for Docker task runs)")
    # Continuing a run whose process died: the checkpoint written after every
    # tool call (agents/checkpoint.py) becomes the conversation so far, and
    # the loop carries on from it under the same run id.
    ap.add_argument("--resume-checkpoint", metavar="RUN_ID",
                     help="Resume this run from its stored checkpoint")

    args = ap.parse_args()

    # Docker task runs only (the launcher appends this flag to the inner command
    # of _start_run_in_docker, never to the local subprocess path): mirror
    # stdout/stderr into the run's log file before anything else prints, so the
    # file carries full output instead of just the header the launcher wrote
    # before starting the container. Parsed ahead of _register_run_start, whose
    # own CLI-only log setup below only fires when AGENT_LOG_FILE is unset —
    # already the case here, so the two never race for sys.stdout.
    if args.write_stdout_to_log:
        _docker_log_file = os.environ.get("AGENT_LOG_FILE")
        if _docker_log_file:
            _setup_docker_log_tee(_docker_log_file)

    # Two distinct workspace channels (they differ when a project subfolder is in
    # play, so neither is redundant):
    #   ws (--workspace) → the directory the agent operates in. From agent_launcher
    #                      this is the absolute project path; from the CLI it is a
    #                      bare workspace name resolved under WORKSPACES_ROOT.
    #   AGENT_WORKSPACE  → the *workspace* name (never the project), read at call
    #                      time for task/agent/shell scoping. Every launcher sets
    #                      it; the CLI does not, so we fall back to cli_ws_name.
    from workspace import resolve_workspace_arg
    ws, cli_ws_name = resolve_workspace_arg(args.workspace)
    # setdefault: when a launcher spawned us it already set AGENT_WORKSPACE to the
    # correct workspace name, so this only fills in the bare-CLI case.
    os.environ.setdefault("AGENT_WORKSPACE", cli_ws_name)

    # Now that the workspace is known, apply its log level (Settings → System →
    # Logging). --verbose forces DEBUG regardless of what the workspace saved.
    import logging
    from common.logging_config import configure_logging
    configure_logging(os.environ.get("AGENT_WORKSPACE"))
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    agent_id = args.agent

    # Fail fast on an unknown agent — before registering a run record or building
    # the agent — so we don't leave a half-started run that crashes in create_agent
    # (mirrors the get_agent guard in runtime/agent_launcher.start_run).
    from agents.registry import get_agent
    if not get_agent(agent_id):
        log.error(f"Unknown agent_id: {agent_id}")
        sys.exit(1)

    # When invoked via CLI without a --run-id, generate one so the run is
    # always tracked regardless of how agent_run.py was invoked.
    run_id = args.run_id or str(uuid4())
    # Published so anything running deeper in the process can name this run
    # without being handed it — the tool hooks put it in the payload they send
    # to operator hook commands (agents/hooks.py).
    os.environ["AGENT_RUN_ID"] = run_id

    # Register the run record early so the dashboard sees pid + log_file
    # immediately, before the (potentially slow) agent build below.
    _register_run_start(run_id, agent_id, ws, args.task_id)

    _state = get_state_transport()
    _heartbeat = _Heartbeat(run_id, _state)
    _heartbeat.start()

    instruction = args.action or args.desc or ""

    # If task_id is provided, enrich the instruction with task context
    # (parent task, sequence siblings, previous run output). Also mark this run
    # as a tracked-task run so taskless delegation (run_agent_tool) is refused
    # — task orchestration must go through the assign/start flow.
    if args.task_id:
        from common.agent_context import current_task_id
        current_task_id.set(str(args.task_id))
        instruction = build_task_instruction(args.task_id, instruction)

    # A resume replays the checkpointed tool trail as the conversation before
    # this turn and asks the model to carry on; the instruction it was started
    # with is the first message of that history.
    _history = None
    _prior_steps = None
    if args.resume_checkpoint:
        from agents import checkpoint as _ckpt
        _cp = None
        try:
            _cp = _state.load_checkpoint(args.resume_checkpoint)
        except Exception:
            _cp = None
        if _cp and _cp.get("steps"):
            _history = _ckpt.history_messages(_cp)
            _prior_steps = _ckpt.resume_steps(_cp)
            if not instruction:
                instruction = str(_cp.get("instruction") or "")
            log.info(f"[resume] continuing run {run_id} from step {_cp.get('step')} "
                     f"({len(_prior_steps)} tool call(s) replayed)")
            instruction = _ckpt.resume_instruction(_cp)
        else:
            log.info(f"[resume] no checkpoint for run {args.resume_checkpoint}; starting over")

    log.info(f"Running agent with instruction: {instruction}")

    # Model config (provider/model/keys/temperature/...) is resolved entirely by
    # create_agent via its cascade (agent definition → workspace override →
    # workspace settings → global .env), so we pass no model overrides here.
    agent_overrides: dict = {"verbose": True} if args.verbose else {}

    # Session continuation: forward tokens/tool events to the SSE broker.
    _extra_callbacks = []
    _sess_id = os.environ.get("AGENT_SESSION_ID")
    _pub_cb = None
    if _sess_id and run_id:
        _port = int(os.environ.get("DASHBOARD_PORT", "8000"))
        _pub_cb = _SessionPublishCallback(_sess_id, run_id, agent_id, _port)
        _extra_callbacks.append(_pub_cb)

    # Checkpoint after every tool boundary, so a process death mid-run is a
    # resume, not a failure. Best-effort like every other write here.
    from agents.checkpoint import RunCheckpointCallback
    _extra_callbacks.append(RunCheckpointCallback(
        run_id, _state.save_checkpoint,
        instruction=(str(_cp.get("instruction") or "") if args.resume_checkpoint and _cp
                     else instruction),
        prior_steps=_prior_steps,
    ))

    def _after_build(agent) -> None:
        log.info(
            f"[agent_init] agent={agent_id}"
            f" provider={agent.provider or 'unknown'}"
            f" model={agent.model or 'unknown'}"
            f" workspace={ws}"
        )
        # Record the *actual* provider/model the agent resolved to (the agent is
        # the source of truth — no need for agent_launcher to pre-resolve into env).
        try:
            _state.update_run(run_id, {"provider": agent.provider or "", "model": agent.model or "", "input": instruction})
        except Exception:
            pass
        # Seed the input context now so the dashboard shows this worker's system
        # prompt while the run is still executing (the full context replaces it
        # at close).
        _state.seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", instruction)
        # Emit a meta event so the frontend knows a new continuation run started.
        if _pub_cb is not None:
            _pub_cb._post({
                "type": "meta", "run_id": run_id, "session_id": _sess_id,
                "agent_id": agent_id, "continuation": True,
            })

    def _emit_summary_and_finalize(result, invocation) -> None:
        callback = invocation.stats
        duration_ms = invocation.duration_ms
        log.info(
            f"[message_summary] "
            f"inbound_tokens={callback.prompt_tokens} "
            f"outbound_tokens={callback.completion_tokens} "
            f"total_tokens={callback.total_tokens} "
            f"tool_calls={callback.tool_calls} "
            f"duration_ms={duration_ms}"
        )
        if run_id:
            _update_run_lifecycle(run_id, args.task_id, result, agent_id=agent_id, process=invocation.process)
        if _pub_cb is not None:
            _pub_cb._post({
                "type": "done",
                "ok": result.ok,
                "response": str(result.agent_output or "") if result.ok else f"Error: {result.error or 'unknown'}",
                "run_id": run_id, "session_id": _sess_id,
                "usage": {
                    "inbound_tokens": callback.prompt_tokens,
                    "outbound_tokens": callback.completion_tokens,
                    "total_tokens": callback.total_tokens,
                    "cached_tokens": getattr(callback, "cached_prompt_tokens", 0),
                },
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
                "continuation": True,
            })

    def _on_build_error(error_msg: str):
        # create_agent failed before any run — finalize the record and bail.
        log.error(f"Unhandled error in run_agent: {error_msg}")
        if run_id:
            class _FailedResult:
                ok = False
                agent_output = None
                error = error_msg
            _update_run_lifecycle(run_id, args.task_id, _FailedResult(), agent_id=agent_id)
        sys.exit(1)

    def _on_success(result, invocation):
        _emit_summary_and_finalize(result, invocation)
        log.info("Agent output:")
        log.info(result.agent_output)
        sys.exit(0)

    def _on_failure(error_msg, invocation):
        _emit_summary_and_finalize(invocation.result, invocation)
        log.info(f"Agent failed: {error_msg}")
        sys.exit(1)

    resume = None
    if args.resume_run:
        import json

        try:
            value = json.loads(args.resume_value) if args.resume_value else None
        except (TypeError, ValueError):
            # A value that will not decode is still an answer someone typed;
            # sending the raw text beats failing the resume.
            value = args.resume_value
        resume = {"run_id": args.resume_run, "value": value, "key": args.resume_key or ""}

    try:
        run_agent_lifecycle(
            agent_id, ws, instruction,
            resume=resume,
            history=_history,
            overrides=agent_overrides,
            extra_callbacks=_extra_callbacks,
            after_build=_after_build,
            on_build_error=_on_build_error,
            on_success=_on_success,
            on_failure=_on_failure,
        )
    finally:
        _heartbeat.stop()
        # Best-effort: a replica or worker on another host can then serve this
        # run's finished log even though it never ran the process itself
        # (common/blobs.py, docs/storage.md). Never raises, so it cannot turn a
        # finished run into a reported failure.
        try:
            from common import blobs
            _log_file = os.environ.get("AGENT_LOG_FILE")
            if _log_file:
                blobs.mirror(blobs.rel(_log_file))
        except Exception:
            pass


if __name__ == "__main__":
    main()
