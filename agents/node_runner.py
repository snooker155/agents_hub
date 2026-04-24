"""
Node Runner – subprocess that runs inside an agent node.

Invoked by node_manager as:
    python -m agents.node_runner \
        --node-id <uuid> --agent-id <str> \
        [--workspace <abs_path>] [--log-file <path>]

Two modes
---------
orchestrator  : polls for unassigned user tasks and runs the orchestrator
                agent on each one.
worker        : polls for tasks whose assigned_agent_type matches this
                agent_id (status = assigned) and runs the agent on them.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Ensure project root is on sys.path when running as a module
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Logging ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_log_fh = None  # set in main() for Docker nodes to mirror stdout into the shared log file

def log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    print(line, flush=True)
    if _log_fh is not None:
        try:
            _log_fh.write(line + "\n")
            _log_fh.flush()
        except Exception:
            pass


def _result_text(value) -> str:
    txt = str(value or "").strip()
    if len(txt) > 100_000:
        txt = txt[:100_000] + "\n...[truncated]"
    return txt


# ── Status helpers ────────────────────────────────────────────────────────────

def _set_status(node_id: str, status: str, exit_code: int | None = None, error: str | None = None) -> None:
    try:
        from agents.node_manager import update_node
        updates: dict = {"status": status}
        if exit_code is not None:
            updates["exit_code"] = exit_code
        if error:
            updates["error"] = error
        if status in ("running", "stopped", "failed", "completed"):
            from datetime import timezone as tz
            updates["finished_at" if status != "running" else "_ts"] = datetime.now(tz.utc).isoformat()
            if status == "running":
                del updates["_ts"]
        update_node(node_id, updates)
    except Exception as exc:
        log(f"[warn] Could not update node status: {exc}")


# ── Orchestrator loop ─────────────────────────────────────────────────────────

def run_orchestrator_loop(node_id: str, workspace: str | None) -> None:
    """Continuously poll for unassigned user tasks and run the orchestrator on each."""
    from common.orchestrator_context import normalize_workspace_name, task_in_workspace
    node_workspace = normalize_workspace_name(workspace) or "default"
    # Tool layer reads AGENT_WORKSPACE to scope visible tasks/agents.
    import os
    os.environ["AGENT_WORKSPACE"] = node_workspace

    log(f"Orchestrator node {node_id[:8]} ready — polling every 10s")
    log(f"Orchestrator workspace scope: {node_workspace}")
    _set_status(node_id, "running")

    POLL = 10  # seconds between sweeps

    while True:
        try:
            from common import tasks_service
            from tasks import TaskStatus, AgentState, CreatedBy
            from agents.agent_factory import create_agent
            from agents.run_manager import (
                _upsert_run,
                _update_run,
                _utc_now_iso,
                get_node_run_logs_dir,
            )

            tasks = tasks_service.list_tasks()

            # Read workspace orchestrator settings
            try:
                from common.workspace import get_workspace_metadata as _get_ws_meta
                _orch_cfg = _get_ws_meta(node_workspace).get("orchestrator", {})
                _followup_mode = _orch_cfg.get("followup_mode", "continuous")
                _enabled = _orch_cfg.get("enabled", True)
            except Exception:
                _followup_mode = "continuous"
                _enabled = True

            # Tasks directly assigned to the orchestrator as a normal worker
            _direct = [
                t for t in tasks
                if t.assigned_agent_type == "orchestrator"
                and t.agent_state == AgentState.assigned
                and task_in_workspace(t, node_workspace)
            ]

            # Tasks where a worker just finished — orchestrator must follow up
            # (finalise, chain next agent, assign reviewer, etc.)
            # Only included when followup_mode == "continuous".
            # Processed even when auto-orchestration is paused: followup is a
            # continuation of an already-orchestrated task, not a new assignment.
            _followup = [
                t for t in tasks
                if t.status in (TaskStatus.reviewed, TaskStatus.in_progress)
                and t.agent_state == AgentState.none
                and task_in_workspace(t, node_workspace)
            ] if _followup_mode == "continuous" else []

            if not _enabled:
                # Auto-orchestration is paused — skip new tasks but still
                # handle directly assigned tasks and in-flight followups.
                pending = _direct + _followup
                if not pending:
                    log("Auto-orchestration is paused — skipping sweep")
                    time.sleep(POLL)
                    continue
            else:
                # New tasks waiting for orchestration
                _new = [
                    t for t in tasks
                    if t.status == TaskStatus.ready
                    and t.agent_state == AgentState.none
                    and t.created_by in (CreatedBy.user, CreatedBy.external)
                    and task_in_workspace(t, node_workspace)
                ]
                pending = _new + _followup + _direct

            for task in pending:
                # Resolve workspace / project path
                abs_ws = workspace
                if task.workspace:
                    try:
                        from common.workspace import resolve_project_root, project_folder_name
                        proj = getattr(task, "project", None) or None
                        if not proj:
                            pid = getattr(task, "project_id", None)
                            if pid:
                                try:
                                    from pathlib import Path as _P
                                    from projects.storage import ProjectStore as _PS
                                    _pr = _PS(_P(__file__).resolve().parents[1] / "projects" / "projects.json")
                                    _obj = _pr.get(str(pid))
                                    if _obj:
                                        proj = project_folder_name(_obj.name)
                                except Exception:
                                    pass
                        abs_ws = str(resolve_project_root(task.workspace, proj))
                    except Exception:
                        pass

                is_followup = task.status in (TaskStatus.reviewed, TaskStatus.in_progress)
                log(f"{'[follow-up]' if is_followup else '[new]'} task {str(task.id)[:8]}: {task.title!r} (status={task.status})")
                run_id = str(uuid4())
                log_file = get_node_run_logs_dir(node_id) / f"agent_run_{run_id}.log"
                try:
                    # Get or create the session for this task
                    _orch_session_id = getattr(task, "session_id", None) or None
                    if not _orch_session_id:
                        try:
                            from common.session_service import get_or_create_task_session as _goc_session
                            _orch_session_id = _goc_session(title=task.title, workspace=task.workspace)
                            tasks_service.update_task(task.id, session_id=_orch_session_id)
                        except Exception:
                            _orch_session_id = None
                    try:
                        from common.session_service import add_run_to_session as _link_run
                        if _orch_session_id:
                            _link_run(_orch_session_id, run_id)
                    except Exception:
                        pass

                    _orch_started_at = _utc_now_iso()
                    if is_followup:
                        prompt = (
                            f"[MONITOR] Task ID: {task.id}\n"
                            f"Title: {task.title}\n"
                            f"Status: {task.status}\n\n"
                            "A previously started agent has finished working on this task. "
                            "Skip Steps 1-4. Go directly to Step 5 of your instructions."
                        )
                    else:
                        prompt = (
                            f"Task ID: {task.id}\n"
                            f"Title: {task.title}\n"
                            f"Description: {task.description or 'No description'}\n\n"
                            "Analyse this task and coordinate its execution by assigning "
                            "appropriate specialized agents."
                        )
                    _upsert_run(
                        {
                            "run_id": run_id,
                            "task_id": str(task.id),
                            "agent_id": "orchestrator",
                            "node_id": node_id,
                            "pid": None,
                            "status": "running",
                            "session_type": "task",
                            "session_id": _orch_session_id,
                            "started_at": _orch_started_at,
                            "finished_at": None,
                            "exit_code": None,
                            "error": None,
                            "log_file": str(log_file),
                            "input": prompt,
                        }
                    )
                    try:
                        tasks_service.upsert_task_execution_log_entry(
                            task.id, run_id,
                            agent_id="orchestrator",
                            status="running",
                            started_at=_orch_started_at,
                            finished_at=None,
                            model="",
                        )
                    except Exception:
                        pass

                    # Link task to this run so agent_state reflects the live status,
                    # and assignment tools can still overwrite it when delegating.
                    tasks_service.update_task(
                        task.id,
                        status=TaskStatus.in_progress,
                        assigned_agent_type="orchestrator",
                        assigned_agent_run_id=run_id,
                    )
                    from common.agent_utils import RunStopCallback
                    from run_agent import RunStatsCallback
                    stop_cb = RunStopCallback(run_id)
                    stats_cb = RunStatsCallback()
                    agent = create_agent("orchestrator", workspace=abs_ws)
                    import time as _time
                    from run_agent import _Tee
                    _run_start_ms = int(_time.time() * 1000)
                    with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                        lf.write(f"[{_now()}] Orchestrator run started\n")
                        lf.write(f"[{_now()}] node_id={node_id} run_id={run_id}\n")
                        lf.write(f"[{_now()}] task_id={task.id}\n")
                        lf.write(f"[{_now()}] workspace={task.workspace or '—'}\n\n")
                        _orig_stdout, _orig_stderr = sys.stdout, sys.stderr
                        sys.stdout = _Tee(_orig_stdout, lf)
                        sys.stderr = _Tee(_orig_stderr, lf)
                        try:
                            result = agent.run(prompt, callbacks=[stop_cb, stats_cb])
                            if stop_cb.cancelled:
                                lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                            elif result.ok:
                                lf.write("\nAgent output:\n")
                                lf.write(_result_text(result.agent_output) + "\n")
                            else:
                                lf.write(f"\nAgent failed: {result.error}\n")
                        finally:
                            sys.stdout = _orig_stdout
                            sys.stderr = _orig_stderr

                    _duration_ms = int(_time.time() * 1000) - _run_start_ms
                    _process = stats_cb.build_process(_duration_ms)
                    _token_usage = _process.get("token_usage") or {}
                    if stop_cb.cancelled:
                        _finished_at = _utc_now_iso()
                        _update_run(
                            run_id,
                            {"status": "stopped", "finished_at": _finished_at, "exit_code": 0, "error": "stopped by user", "process": _process},
                        )
                        try:
                            tasks_service.upsert_task_execution_log_entry(
                                task.id, run_id, agent_id="orchestrator", status="stopped",
                                finished_at=_finished_at, exit_code=0,
                            )
                        except Exception:
                            pass
                        log(f"Task {str(task.id)[:8]} stopped by user")
                        continue
                    if result.ok:
                        _finished_at = _utc_now_iso()
                        _update_run(
                            run_id,
                            {"status": "completed", "finished_at": _finished_at, "exit_code": 0, "process": _process, "output": _result_text(result.agent_output)},
                        )
                        try:
                            tasks_service.upsert_task_execution_log_entry(
                                task.id, run_id, agent_id="orchestrator", status="completed",
                                finished_at=_finished_at, exit_code=0,
                                inbound_tokens=int(_token_usage.get("inbound_tokens") or 0),
                                outbound_tokens=int(_token_usage.get("outbound_tokens") or 0),
                                total_tokens=int(_token_usage.get("total_tokens") or 0),
                            )
                        except Exception:
                            pass
                        try:
                            from run_agent import _collect_changed_files
                            from agents.run_manager import get_run_by_id as _get_run
                            started_at = (_get_run(run_id) or {}).get("started_at")
                            changed_files = _collect_changed_files(str(task.id), started_at)
                            agent_output = (result.agent_output or "").strip()
                            if len(agent_output) > 100_000:
                                agent_output = agent_output[:100_000] + "\n...[truncated]"
                            if agent_output or changed_files:
                                tasks_service.set_task_result(
                                    task.id, agent_output,
                                    files=changed_files, run_id=run_id, agent_id="orchestrator",
                                )
                        except Exception as _e:
                            log(f"[warn] Could not store orchestrator task result: {_e}")
                        latest = tasks_service.get_task(task.id)
                        delegated = bool(latest and latest.assigned_agent_type and latest.assigned_agent_type != "orchestrator")
                        # If no delegation happened, return task to queue —
                        # but only if the task is still in_progress (i.e. user hasn't manually
                        # rejected or moved it away while the orchestrator was running).
                        if not delegated:
                            if latest and latest.assigned_agent_type == "orchestrator":
                                tasks_service.clear_agent(task.id)
                            if latest and latest.status == TaskStatus.in_progress:
                                if is_followup:
                                    # Safety net: orchestrator should have called update_task(resolved)
                                    # in Step 5. If it didn't, auto-resolve here to prevent the task
                                    # from looping back through the _followup filter indefinitely.
                                    tasks_service.update_task(task.id, status=TaskStatus.resolved)
                                elif latest.agent_state == AgentState.none:
                                    # Race condition: worker finished during this orchestrator run.
                                    # run_manager already set in_progress + agent_state=none as the
                                    # followup signal. Don't reset to ready — let _followup pick it up.
                                    pass
                                else:
                                    tasks_service.update_task(task.id, status=TaskStatus.ready)
                        log(f"Task {str(task.id)[:8]} orchestrated successfully")
                    else:
                        _finished_at = _utc_now_iso()
                        _update_run(
                            run_id,
                            {
                                "status": "failed",
                                "finished_at": _finished_at,
                                "exit_code": 1,
                                "error": str(result.error or "orchestrator error"),
                                "process": _process,
                            },
                        )
                        try:
                            tasks_service.upsert_task_execution_log_entry(
                                task.id, run_id, agent_id="orchestrator", status="failed",
                                finished_at=_finished_at, exit_code=1,
                                error=str(result.error or "orchestrator error"),
                            )
                        except Exception:
                            pass
                        log(f"Task {str(task.id)[:8]} orchestration error: {result.error}")
                        latest = tasks_service.get_task(task.id)
                        if latest and latest.assigned_agent_type == "orchestrator":
                            tasks_service.clear_agent(task.id)
                        tasks_service.block_task(task.id, reason=result.error or "orchestrator error")
                except Exception as exc:
                    log(f"Error on task {str(task.id)[:8]}: {exc}")
                    try:
                        _update_run(
                            run_id,
                            {
                                "status": "failed",
                                "finished_at": _utc_now_iso(),
                                "exit_code": 1,
                                "error": str(exc),
                            },
                        )
                    except Exception:
                        pass
                    try:
                        latest = tasks_service.get_task(task.id)
                        if latest and latest.assigned_agent_type == "orchestrator":
                            tasks_service.clear_agent(task.id)
                    except Exception:
                        pass

        except Exception as exc:
            log(f"Loop error: {exc}\n{traceback.format_exc()}")

        time.sleep(POLL)


# ── Worker loop ───────────────────────────────────────────────────────────────

def run_worker_loop(node_id: str, agent_id: str, workspace: str | None) -> None:
    """Poll for tasks whose assigned_agent_type == agent_id and run them."""
    from common.orchestrator_context import normalize_workspace_name, task_in_workspace
    node_workspace = normalize_workspace_name(workspace) or "default"
    import os
    os.environ["AGENT_WORKSPACE"] = node_workspace

    log(f"Worker node {node_id[:8]} ({agent_id}) ready — polling every 10s")
    log(f"Worker workspace scope: {node_workspace}")
    _set_status(node_id, "running")

    POLL = 10

    while True:
        try:
            from common import tasks_service
            from tasks import TaskStatus, AgentState
            from agents.agent_factory import create_agent

            # Check execution_mode — skip if the workspace uses subprocess-based execution
            try:
                from common.workspace import get_workspace_metadata as _get_ws_meta
                _execution_mode = _get_ws_meta(node_workspace).get("orchestrator", {}).get("execution_mode", "subprocess")
            except Exception:
                _execution_mode = "subprocess"
            if _execution_mode != "node":
                log("Execution mode is 'subprocess' — worker node is idle")
                time.sleep(POLL)
                continue

            tasks = tasks_service.list_tasks()
            assigned = [
                t for t in tasks
                if t.assigned_agent_type == agent_id
                and t.agent_state == AgentState.assigned
                and task_in_workspace(t, node_workspace)
            ]

            for task in assigned:
                abs_ws = workspace
                if task.workspace:
                    try:
                        from common.workspace import resolve_project_root, project_folder_name
                        proj = getattr(task, "project", None) or None
                        if not proj:
                            pid = getattr(task, "project_id", None)
                            if pid:
                                try:
                                    from pathlib import Path as _P
                                    from projects.storage import ProjectStore as _PS
                                    _pr = _PS(_P(__file__).resolve().parents[1] / "projects" / "projects.json")
                                    _obj = _pr.get(str(pid))
                                    if _obj:
                                        proj = project_folder_name(_obj.name)
                                except Exception:
                                    pass
                        abs_ws = str(resolve_project_root(task.workspace, proj))
                    except Exception:
                        pass

                log(f"Processing task {str(task.id)[:8]}: {task.title!r}")
                try:
                    from common.agent_utils import RunStopCallback
                    from run_agent import RunStatsCallback
                    from agents.run_manager import (
                        _update_run,
                        _utc_now_iso,
                        get_node_run_logs_dir,
                    )
                    import time as _time
                    # Reuse the "assigned" run record created at assignment time.
                    run_id = str(task.assigned_agent_run_id or uuid4())
                    log_file = get_node_run_logs_dir(node_id) / f"agent_run_{run_id}.log"
                    _worker_started_at = _utc_now_iso()
                    prompt = f"{task.title}\n\n{task.description or ''}"
                    _update_run(run_id, {
                        "status": "running",
                        "node_id": node_id,
                        "started_at": _worker_started_at,
                        "log_file": str(log_file),
                        "input": prompt,
                    })
                    try:
                        tasks_service.upsert_task_execution_log_entry(
                            task.id, run_id,
                            agent_id=agent_id,
                            status="running",
                            started_at=_worker_started_at,
                            finished_at=None,
                        )
                    except Exception:
                        pass
                    # Ensure session exists and the run is linked to it
                    try:
                        from agents.run_manager import get_run_by_id as _get_run_rec
                        from common.session_service import get_or_create_task_session, add_run_to_session
                        _run_rec = _get_run_rec(run_id) or {}
                        _session_id = _run_rec.get("session_id") or getattr(task, "session_id", None) or None
                        if not _session_id:
                            _session_id = get_or_create_task_session(title=task.title, workspace=task.workspace)
                            tasks_service.update_task(task.id, session_id=_session_id)
                            from agents.run_manager import _update_run as _upd
                            _upd(run_id, {"session_id": _session_id})
                        add_run_to_session(_session_id, run_id)
                    except Exception:
                        pass
                    stop_cb = RunStopCallback(run_id)
                    stats_cb = RunStatsCallback()
                    tasks_service.update_task(task.id, status=TaskStatus.in_progress)
                    agent = create_agent(agent_id, workspace=abs_ws)
                    from run_agent import _Tee
                    _run_start_ms = int(_time.time() * 1000)
                    with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                        lf.write(f"[{_now()}] Worker run started\n")
                        lf.write(f"[{_now()}] node_id={node_id} run_id={run_id}\n")
                        lf.write(f"[{_now()}] task_id={task.id}\n\n")
                        _orig_stdout, _orig_stderr = sys.stdout, sys.stderr
                        sys.stdout = _Tee(_orig_stdout, lf)
                        sys.stderr = _Tee(_orig_stderr, lf)
                        try:
                            result = agent.run(prompt, callbacks=[stop_cb, stats_cb])
                            if stop_cb.cancelled:
                                lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                            elif result.ok:
                                lf.write("\nAgent output:\n")
                                lf.write(_result_text(result.agent_output) + "\n")
                            else:
                                lf.write(f"\nAgent failed: {result.error}\n")
                        finally:
                            sys.stdout = _orig_stdout
                            sys.stderr = _orig_stderr

                    _duration_ms = int(_time.time() * 1000) - _run_start_ms
                    _process = stats_cb.build_process(_duration_ms)
                    from agents.run_manager import _update_run, finalize_task_from_run
                    if stop_cb.cancelled:
                        _update_run(run_id, {"status": "stopped", "finished_at": _utc_now_iso(), "exit_code": 0, "error": "stopped by user", "process": _process})
                        log(f"Task {str(task.id)[:8]} stopped by user")
                        tasks_service.clear_agent(task.id)
                        tasks_service.stop_task(task.id)
                    elif result.ok:
                        _agent_out = _result_text(result.agent_output)
                        _update_run(run_id, {"status": "completed", "finished_at": _utc_now_iso(), "exit_code": 0, "process": _process, "output": _agent_out})
                        log(f"Task {str(task.id)[:8]} completed")
                        try:
                            from run_agent import _collect_changed_files
                            from agents.run_manager import get_run_by_id as _get_run
                            started_at = (_get_run(run_id) or {}).get("started_at")
                            changed_files = _collect_changed_files(str(task.id), started_at)
                            agent_output = _agent_out
                            if agent_output or changed_files:
                                tasks_service.set_task_result(
                                    task.id, agent_output,
                                    files=changed_files, run_id=run_id, agent_id=agent_id,
                                )
                        except Exception as _e:
                            log(f"[warn] Could not store task result: {_e}")
                        finalize_task_from_run(run_id, "completed", 0)
                    else:
                        _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(), "exit_code": 1, "error": str(result.error or "worker error"), "process": _process})
                        log(f"Task {str(task.id)[:8]} failed: {result.error}")
                        finalize_task_from_run(run_id, "failed", 1)
                except Exception as exc:
                    log(f"Error on task {str(task.id)[:8]}: {exc}")
                    try:
                        from agents.run_manager import _update_run, finalize_task_from_run
                        _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(), "exit_code": 1, "error": str(exc)})
                        finalize_task_from_run(run_id, "failed", 1)
                    except Exception:
                        pass

        except Exception as exc:
            log(f"Loop error: {exc}")

        time.sleep(POLL)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Agent node runner")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--write-stdout-to-log", action="store_true",
                        help="Mirror stdout into --log-file (used for Docker nodes)")
    parser.add_argument("--service-mode", action="store_true",
                        help="Run as an HTTP service only — no task polling loop")
    parser.add_argument("--http-port", type=int, default=None,
                        help="If set, start an HTTP server on this port inside the container")
    args = parser.parse_args()

    if args.write_stdout_to_log and args.log_file:
        global _log_fh
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_fh = open(log_path, "a", encoding="utf-8", buffering=1)

    log(f"=== Node {args.node_id[:8]} starting  agent={args.agent_id} ===")

    if args.service_mode:
        # ── Service mode: HTTP server is the entire process, no task polling ──
        if not args.http_port:
            log("ERROR: --service-mode requires --http-port")
            sys.exit(1)
        log(f"=== Node {args.node_id[:8]} running as HTTP SERVICE on port {args.http_port} ===")
        _set_status(args.node_id, "running")
        try:
            from agents.http_server import start_http_server
            start_http_server(
                args.node_id,
                args.agent_id,
                args.http_port,
                workspace=args.workspace,
                log_file=args.log_file,
            )
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            log(f"Fatal: {exc}\n{traceback.format_exc()}")
            _set_status(args.node_id, "failed", exit_code=1, error=str(exc))
            sys.exit(1)
        _set_status(args.node_id, "stopped", exit_code=0)
    else:
        # ── Worker mode: optional HTTP sidecar + task polling loop ────────────
        if args.http_port:
            try:
                from agents.http_server import start_http_server_thread
                start_http_server_thread(
                    args.node_id,
                    args.agent_id,
                    args.http_port,
                    workspace=args.workspace,
                    log_file=args.log_file,
                )
                log(f"HTTP server started on port {args.http_port}")
            except Exception as exc:
                log(f"[warn] Could not start HTTP server: {exc}")

        try:
            if args.agent_id == "orchestrator":
                run_orchestrator_loop(args.node_id, args.workspace)
            else:
                run_worker_loop(args.node_id, args.agent_id, args.workspace)
        except KeyboardInterrupt:
            log("Interrupted")
            _set_status(args.node_id, "stopped", exit_code=0)
        except Exception as exc:
            log(f"Fatal: {exc}\n{traceback.format_exc()}")
            _set_status(args.node_id, "failed", exit_code=1, error=str(exc))
            sys.exit(1)


if __name__ == "__main__":
    main()
