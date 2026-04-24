import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

from agents.stats_callback import RunStatsCallback
from common.paths import PROJECTS_FILE

_INTERNAL_PREFIXES = (".logs/", ".progress.json", ".task_result")


# -------------------- Log tee --------------------

class _Tee:
    """Mirror writes to both the original stream and a log file."""

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file

    def write(self, data):
        self._stream.write(data)
        self._log.write(data)
        self._log.flush()

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def fileno(self):
        return self._stream.fileno()

    def isatty(self):
        return False


def _setup_cli_log(run_id: str, workspace: str) -> str:
    """Create an agent_run_<id>.log file in the state run_logs folder and tee
    sys.stdout / sys.stderr to it so CLI runs are captured just like
    subprocess runs spawned by worker_runner.  Returns the log file path."""
    from agents.run_manager import STATE_DIR
    log_dir = STATE_DIR / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"agent_run_{run_id}.log"
    lf = open(log_path, "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    try:
        from agents.run_manager import _utc_now_iso
        lf.write(f"--- Run started at {_utc_now_iso()} ---\n\n")
    except Exception:
        pass
    sys.stdout = _Tee(sys.__stdout__, lf)
    sys.stderr = _Tee(sys.__stderr__, lf)
    return str(log_path)


# -------------------- Session publish callback (continuation runs) --------------------

class _SessionPublishCallback(BaseCallbackHandler):
    """Posts streaming events to the backend SSE broker when this subprocess
    is a session continuation (AGENT_SESSION_ID env var is set).

    Called from a background thread by LangChain, so HTTP POSTs are synchronous
    (requests library). The local dashboard server is expected to be running.
    """

    raise_error: bool = False  # never crash the agent run because of publish failures

    def __init__(self, session_id: str, run_id: str, agent_id: str, port: int = 8000):
        self.session_id = session_id
        self.run_id = run_id
        self.agent_id = agent_id
        self._url = f"http://localhost:{port}/api/sessions/{session_id}/events"
        self._step = 0

    def _post(self, event: dict) -> None:
        try:
            import requests as _req
            _req.post(self._url, json=event, timeout=2)
        except Exception:
            pass

    def on_llm_new_token(self, token, **_):
        if token:
            self._post({"type": "token", "token": token, "run_id": self.run_id})

    def on_tool_start(self, serialized, input_str, **_):
        self._step += 1
        name = (serialized or {}).get("name", "tool") if isinstance(serialized, dict) else "tool"
        inp = str(input_str or "")
        self._post({
            "type": "tool_start",
            "step": self._step,
            "tool": name,
            "input": inp[:240],
            "run_id": self.run_id,
        })

    def on_tool_end(self, output, **_):
        self._post({
            "type": "tool_end",
            "output": str(output or "")[:240],
            "run_id": self.run_id,
        })

    def on_tool_error(self, error, **_):
        self._post({
            "type": "tool_error",
            "error": str(error),
            "run_id": self.run_id,
        })


# -------------------- Workspace file tracking --------------------

def _collect_changed_files(task_id: str, started_at_iso: Optional[str]) -> list:
    """Return project files modified at or after the run's started_at time.

    Scans the project subfolder (.agents_hub/workspaces/{ws}/{project}/) when a project is
    set on the task, so only project-level files appear in the task Files tab.
    Falls back to the workspace root when no project is set."""
    if not started_at_iso:
        return []
    try:
        from datetime import datetime, timezone
        from common import tasks_service as _ts
        from uuid import UUID as _UUID
        from workspace import resolve_project_root, project_folder_name
        task = _ts.get_task(_UUID(str(task_id)))
        ws_name = (getattr(task, "workspace", None) or "").strip() if task else ""
        if not ws_name:
            return []
        project_name = (getattr(task, "project", None) or "").strip() if task else ""
        if not project_name:
            pid = getattr(task, "project_id", None) if task else None
            if pid:
                try:
                    from projects.storage import ProjectStore as _PS
                    _pstore = _PS(PROJECTS_FILE)
                    _proj = _pstore.get(str(pid))
                    if _proj:
                        project_name = project_folder_name(_proj.name)
                except Exception:
                    pass
        root = resolve_project_root(ws_name, project_name or None)
        started_at = datetime.fromisoformat(started_at_iso)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        files = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except Exception:
                continue
            if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in _INTERNAL_PREFIXES):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if mtime >= started_at:
                    files.append(rel)
            except Exception:
                pass
        files.sort()
        return files
    except Exception:
        return []


# -------------------- Run lifecycle --------------------

def _register_run_start(run_id: str, agent_id: str, task_id: Optional[str]) -> Optional[str]:
    """Create the run record at subprocess startup via run_manager.open_run."""
    try:
        from agents.run_manager import open_run
        open_run(
            run_id,
            agent_id,
            task_id=str(task_id) if task_id else None,
            session_id=os.environ.get("AGENT_SESSION_ID") or None,
            session_type="task",
            pid=os.getpid(),
            log_file=os.environ.get("AGENT_LOG_FILE") or None,
            execution_mode=os.environ.get("AGENT_EXECUTION_MODE", "local"),
            provider=os.environ.get("AGENT_PROVIDER", ""),
            model=os.environ.get("AGENT_MODEL", ""),
        )
        return run_id
    except Exception:
        pass


def _update_run_lifecycle(run_id: str, task_id, result, agent_id: str = "", process: Optional[dict] = None) -> None:
    """Update run state, persist task result, and finalize task status."""
    try:
        from agents.run_manager import close_run, finalize_task_from_run, get_run_by_id

        status = "completed" if result.ok else "failed"
        exit_code = 0 if result.ok else 1
        agent_output = (result.agent_output or "").strip() if result.ok else None
        close_run(
            run_id,
            status=status,
            exit_code=exit_code,
            error=None if result.ok else str(result.error),
            output=agent_output,
            **({"process": process} if process else {}),
        )

        if result.ok and task_id:
            try:
                from common import tasks_service
                from uuid import UUID
                run_rec = get_run_by_id(run_id)
                started_at = (run_rec or {}).get("started_at")
                changed_files = _collect_changed_files(task_id, started_at)
                agent_output = (result.agent_output or "").strip()
                if len(agent_output) > 100_000:
                    agent_output = agent_output[:100_000] + "\n...[truncated]"
                if agent_output or changed_files:
                    tasks_service.set_task_result(
                        UUID(task_id), agent_output,
                        files=changed_files, run_id=run_id, agent_id=agent_id or None,
                    )
            except Exception:
                pass

        try:
            finalize_task_from_run(run_id, status, exit_code)
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

    if args.workspace:
        os.environ["WORKSPACE_ROOT"] = args.workspace

    from common.utils import ensure_dirs
    from agents.agent_factory import create_agent
    ensure_dirs()

    ws = args.workspace or os.environ.get("WORKSPACE_ROOT", "./out")
    agent_id = args.agent

    # When invoked via CLI without a --run-id, generate one so the run is
    # always tracked regardless of how run_agent.py was invoked.
    from uuid import uuid4 as _uuid4
    run_id = args.run_id or str(_uuid4())

    # When invoked via CLI (not as a subprocess of worker_runner), no log file
    # is set up externally — create one and tee stdout/stderr to it.
    if not os.environ.get("AGENT_LOG_FILE"):
        os.environ["AGENT_LOG_FILE"] = _setup_cli_log(run_id, ws)

    # Register (or update) the run record so the dashboard sees pid + log_file
    _register_run_start(run_id, agent_id, args.task_id)

    # Apply per-agent model overrides injected by agent_runner via environment variables
    agent_overrides: dict = {}
    if os.environ.get("AGENT_PROVIDER"):
        agent_overrides["provider"] = os.environ["AGENT_PROVIDER"]
    if os.environ.get("AGENT_MODEL"):
        agent_overrides["model"] = os.environ["AGENT_MODEL"]
    if os.environ.get("AGENT_BASE_URL"):
        agent_overrides["base_url"] = os.environ["AGENT_BASE_URL"]
    if os.environ.get("AGENT_API_KEY"):
        agent_overrides["api_key"] = os.environ["AGENT_API_KEY"]
    if os.environ.get("AGENT_TEMPERATURE"):
        agent_overrides["temperature"] = float(os.environ["AGENT_TEMPERATURE"])
    if os.environ.get("AGENT_MAX_TOKENS"):
        agent_overrides["max_tokens"] = int(os.environ["AGENT_MAX_TOKENS"])

    if args.verbose:
        agent_overrides["verbose"] = True
    agent = create_agent(agent_id, workspace=ws, **agent_overrides)
    print(
        f"[agent_init] agent={agent_id}"
        f" provider={agent.provider or 'unknown'}"
        f" model={agent.model or 'unknown'}"
        f" workspace={ws}"
    )

    instruction = args.action or args.desc or ""

    # If task_id is provided, set env var and enrich the instruction with task context
    if args.task_id:
        try:
            from uuid import UUID
            from common import tasks_service
            task = tasks_service.get_task(UUID(args.task_id))
            if task:
                title_prefix = f"Task ID: {args.task_id}\nTask: {task.title}\n\n" if task.title and task.title not in instruction else f"Task ID: {args.task_id}\n\n"
                if not instruction:
                    desc = task.description or ""
                    instruction = f"{title_prefix}{desc}".strip() or f"Process task {args.task_id}"
                elif title_prefix:
                    instruction = f"{title_prefix}{instruction}"

                # If this is a subtask, prepend the parent task's title+description so the
                # agent understands the big-picture goal.
                try:
                    if task.parent_id:
                        parent = tasks_service.get_task(task.parent_id)
                        if parent:
                            parent_ctx = f"Task: {parent.title}"
                            if parent.description:
                                parent_ctx += f"\n{parent.description}"
                            instruction = (
                                f"{'=' * 60}\n"
                                f"PARENT TASK (overall goal)\n"
                                f"{'=' * 60}\n"
                                f"{parent_ctx}\n"
                                f"{'=' * 60}\n\n"
                                f"{instruction}"
                            )
                except Exception:
                    pass

                # If this subtask is part of a sequence, append results of all prior
                # completed siblings (lower order) so the agent has full chain context.
                try:
                    if task.sequence_id and task.order is not None:
                        all_tasks = tasks_service.list_tasks()
                        prior_siblings = sorted(
                            [
                                t for t in all_tasks
                                if t.sequence_id == task.sequence_id
                                and t.order is not None
                                and t.order < task.order
                            ],
                            key=lambda t: t.order,
                        )
                        prior_results = []
                        for sib in prior_siblings:
                            sib_result = tasks_service.get_task_result(sib.id)
                            if sib_result and sib_result.strip():
                                prior_results.append(
                                    f"[Step {sib.order}: {sib.title}]\n{sib_result.strip()}"
                                )
                        if prior_results:
                            instruction = (
                                f"\n"
                                f"{instruction}\n\n"
                                f"{'=' * 60}\n"
                                f"RESULTS FROM PREVIOUS STEPS IN THIS SEQUENCE\n"
                                f"{'=' * 60}\n"
                                + "\n\n".join(prior_results)
                                + f"\n{'=' * 60}\n"
                                f"\nContinue from where the previous step left off."
                            )
                except Exception:
                    pass

                # Append previous agent output on this same task (re-run scenario).
                # Skip orchestrator results — they are assignment summaries, not work output.
                try:
                    _ROUTING_AGENTS = {"orchestrator", "decomposer"}
                    all_results = tasks_service.get_task_results(UUID(args.task_id))
                    prev_entry = next(
                        (e for e in reversed(all_results) if e.get("agent_id") not in _ROUTING_AGENTS),
                        None,
                    )
                    prev_result = (prev_entry or {}).get("result", "") or ""
                    if prev_result.strip():
                        instruction = (
                            f"\n"
                            f"{instruction}\n\n"
                            f"{'=' * 60}\n"
                            f"OUTPUT FROM PREVIOUS AGENT RUN ON THIS TASK\n"
                            f"{'=' * 60}\n"
                            f"{prev_result.strip()}\n"
                            f"{'=' * 60}\n"
                            f"\nContinue the work based on the above output."
                        )
                except Exception:
                    pass
        except Exception:
            pass

    if not instruction:
        instruction = f"Process task {args.task_id or ''}"

    # Store the input on the run record so it's queryable without parsing logs
    if run_id:
        try:
            from agents.run_manager import update_run
            update_run(run_id, {"input": instruction})
        except Exception:
            pass

    print(f"Running agent with instruction: {instruction}")

    callback = RunStatsCallback()
    callbacks = [callback]

    # If this is a session continuation subprocess, attach a publish callback so
    # tokens/tool events are forwarded to the SSE broker via HTTP POST.
    _sess_id = os.environ.get("AGENT_SESSION_ID")
    if _sess_id and run_id:
        _port = int(os.environ.get("DASHBOARD_PORT", "8000"))
        _pub_cb = _SessionPublishCallback(_sess_id, run_id, agent_id, _port)
        callbacks.append(_pub_cb)
        # Emit a meta event so the frontend knows a new continuation run started.
        _pub_cb._post({
            "type": "meta",
            "run_id": run_id,
            "session_id": _sess_id,
            "agent_id": agent_id,
            "continuation": True,
        })

    t0 = time.perf_counter()
    try:
        result = agent.run(instruction, callbacks=callbacks)
    except Exception as _exc:
        import traceback as _tb
        duration_ms = int((time.perf_counter() - t0) * 1000)
        error_text = f"Unhandled error in run_agent: {_exc}\n{_tb.format_exc()}"
        print(error_text, file=sys.stderr)
        if run_id:
            # Build a synthetic failed result so lifecycle cleanup runs normally
            class _FailedResult:
                ok = False
                agent_output = None
                error = error_text
            _update_run_lifecycle(run_id, args.task_id, _FailedResult(), agent_id=agent_id, process=callback.build_process(duration_ms))
        sys.exit(1)

    duration_ms = int((time.perf_counter() - t0) * 1000)

    print(
        f"[message_summary] "
        f"inbound_tokens={callback.prompt_tokens} "
        f"outbound_tokens={callback.completion_tokens} "
        f"total_tokens={callback.total_tokens} "
        f"tool_calls={callback.tool_calls} "
        f"duration_ms={duration_ms}"
    )

    if run_id:
        _update_run_lifecycle(run_id, args.task_id, result, agent_id=agent_id, process=callback.build_process(duration_ms))

    # Emit done event to session SSE stream (continuation runs only)
    if _sess_id and run_id:
        _pub_cb._post({
            "type": "done",
            "ok": result.ok,
            "response": str(result.agent_output or "") if result.ok else f"Error: {result.error or 'unknown'}",
            "run_id": run_id,
            "session_id": _sess_id,
            "usage": {
                "inbound_tokens": callback.prompt_tokens,
                "outbound_tokens": callback.completion_tokens,
                "total_tokens": callback.total_tokens,
            },
            "tool_calls": callback.tool_calls,
            "duration_ms": duration_ms,
            "continuation": True,
        })


    if result.ok:
        print("Agent output:")
        print(result.agent_output)
        sys.exit(0)
    else:
        print(f"Agent failed: {result.error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
