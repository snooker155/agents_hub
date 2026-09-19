import argparse
import os
import sys
from pathlib import Path
from typing import Optional
from uuid import uuid4

# This entrypoint is spawned as a subprocess with cwd set to the workspace
# directory, so the repo root is not on sys.path by default. Put it there before
# the first-party imports below (mirrors runtime/node_run.py and runtime/flow_run.py).
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.paths import AGENTS_HUB_ROOT
from managers.run_manager import (
    _utc_now_iso,
    close_run_from_result,
    finalize_task_from_run,
    park_task_awaiting_input,
    open_run,
    update_run,
)
from agents.agent_lifecycle import run_agent_lifecycle
from agents.callbacks import SessionPublishCallback as _SessionPublishCallback
from common.utils import Tee as _Tee  # Tee re-exported for back-compat (was defined here)
from tasks.context import build_task_instruction, persist_task_result


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


# -------------------- Run lifecycle --------------------

def _register_run_start(run_id: str, agent_id: str, ws: str, task_id: Optional[str]) -> Optional[str]:
    """Create the run record at subprocess startup via run_manager.open_run."""
    try:
        # When invoked via CLI (not as a subprocess of agent_launcher), no log file
        # is set up externally — create one and tee stdout/stderr to it.
        if not os.environ.get("AGENT_LOG_FILE"):
            os.environ["AGENT_LOG_FILE"] = _setup_cli_log(run_id, ws)

        open_run(
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
        )
        return run_id
    except Exception:
        pass


def _update_run_lifecycle(run_id: str, task_id, result, agent_id: str = "", process: Optional[dict] = None) -> None:
    """Update run state, persist task result, and finalize task status."""
    try:
        close_run_from_result(run_id, result, **({"process": process} if process else {}))

        # The agent paused via ask_user: persist the question as the task result so
        # it's visible (and picked up as prior context on resume), then park the
        # task in awaiting_input instead of resolving it. No finalize → any waiting
        # session continuation stays pending until the user answers.
        if getattr(result, "status", "") == "awaiting_input":
            if task_id:
                persist_task_result(task_id, run_id, result.agent_output or "", agent_id=agent_id)
                park_task_awaiting_input(run_id, getattr(result, "pending_question", None) or {}, agent_id=agent_id)
            return

        if result.ok and task_id:
            persist_task_result(task_id, run_id, result.agent_output or "", agent_id=agent_id)

        try:
            finalize_task_from_run(run_id, "completed" if result.ok else "failed", 0 if result.ok else 1)
        except Exception:
            pass
    except Exception:
        pass


# -------------------- Entry point --------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", help="Agent ID from definitions (e.g. pm_agent, swe_agent)")
    ap.add_argument("action", nargs="?", help="Optional action/instruction")
    ap.add_argument("--desc", help="Initial description for the task")
    ap.add_argument("--task-id", help="Optional task ID to associate with")
    ap.add_argument("--run-id", help="Optional run ID to update lifecycle state for")
    ap.add_argument("--workspace", help="Path to workspace")
    ap.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args()

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
        print(f"Unknown agent_id: {agent_id}", file=sys.stderr)
        sys.exit(1)

    # When invoked via CLI without a --run-id, generate one so the run is
    # always tracked regardless of how agent_run.py was invoked.
    run_id = args.run_id or str(uuid4())

    # Register the run record early so the dashboard sees pid + log_file
    # immediately, before the (potentially slow) agent build below.
    _register_run_start(run_id, agent_id, ws, args.task_id)

    instruction = args.action or args.desc or ""

    # If task_id is provided, enrich the instruction with task context
    # (parent task, sequence siblings, previous run output). Also mark this run
    # as a tracked-task run so taskless delegation (run_agent_tool) is refused
    # — task orchestration must go through the assign/start flow.
    if args.task_id:
        from common.agent_context import current_task_id
        current_task_id.set(str(args.task_id))
        instruction = build_task_instruction(args.task_id, instruction)

    print(f"Running agent with instruction: {instruction}")

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

    def _after_build(agent) -> None:
        print(
            f"[agent_init] agent={agent_id}"
            f" provider={agent.provider or 'unknown'}"
            f" model={agent.model or 'unknown'}"
            f" workspace={ws}"
        )
        # Record the *actual* provider/model the agent resolved to (the agent is
        # the source of truth — no need for agent_launcher to pre-resolve into env).
        try:
            update_run(run_id, {"provider": agent.provider or "", "model": agent.model or "", "input": instruction})
        except Exception:
            pass
        # Seed the input context now so the dashboard shows this worker's system
        # prompt while the run is still executing (the full context replaces it
        # at close).
        from managers.run_manager import seed_run_input_context
        seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", instruction)
        # Emit a meta event so the frontend knows a new continuation run started.
        if _pub_cb is not None:
            _pub_cb._post({
                "type": "meta", "run_id": run_id, "session_id": _sess_id,
                "agent_id": agent_id, "continuation": True,
            })

    def _emit_summary_and_finalize(result, invocation) -> None:
        callback = invocation.stats
        duration_ms = invocation.duration_ms
        print(
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
        print(f"Unhandled error in run_agent: {error_msg}", file=sys.stderr)
        if run_id:
            class _FailedResult:
                ok = False
                agent_output = None
                error = error_msg
            _update_run_lifecycle(run_id, args.task_id, _FailedResult(), agent_id=agent_id)
        sys.exit(1)

    def _on_success(result, invocation):
        _emit_summary_and_finalize(result, invocation)
        print("Agent output:")
        print(result.agent_output)
        sys.exit(0)

    def _on_failure(error_msg, invocation):
        _emit_summary_and_finalize(invocation.result, invocation)
        print(f"Agent failed: {error_msg}")
        sys.exit(1)

    run_agent_lifecycle(
        agent_id, ws, instruction,
        overrides=agent_overrides,
        extra_callbacks=_extra_callbacks,
        after_build=_after_build,
        on_build_error=_on_build_error,
        on_success=_on_success,
        on_failure=_on_failure,
    )


if __name__ == "__main__":
    main()
