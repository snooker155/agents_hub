"""
Node Runner – subprocess that runs inside an agent node.

Invoked by node_manager as:
    python -m runtime.node_run \
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
from common.paths import PROJECTS_FILE

# Ensure project root is on sys.path when running as a module
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Live streaming ───────────────────────────────────────────────────────────

def _session_publisher(session_id, run_id: str, agent_id: str) -> list:
    """Live-stream a node run into its session's SSE channel.

    Returns a one-element callback list, or an empty one when the run has no
    session, so callers can splat it into ``extra_callbacks`` unconditionally.
    The ``meta`` marker mirrors what runtime/agent_run.py posts for subprocess
    runs, so a dashboard watching the session opens a live block the same way
    whichever runner is executing the task. Token events only materialise when
    the agent was built with streaming on (the Settings toggle); with it off the
    channel still carries tool and thinking events.
    """
    if not session_id:
        return []
    import os
    from agents.callbacks import SessionPublishCallback
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    cb = SessionPublishCallback(str(session_id), run_id, agent_id, port)
    cb._post({"type": "meta", "run_id": run_id, "session_id": str(session_id),
              "agent_id": agent_id, "continuation": True})
    return [cb]


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
        from managers.node_manager import update_node
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
    from common.workspace_context import normalize_workspace_name, task_in_workspace
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
            # A message addressed to this copy jumps the task queue: someone is
            # waiting on the other end of it.
            if _drain_instance_inbox(node_id, agent_id, workspace):
                continue
            from tasks import service as tasks_service
            from tasks import TaskStatus, AgentState, CreatedBy
            from agents.agent_factory import create_agent
            from managers.run_manager import (
                _upsert_run,
                _update_run,
                _utc_now_iso,
                run_log_path,
            )

            tasks = tasks_service.list_tasks()

            # Read workspace orchestrator settings
            try:
                from workspace import get_workspace_metadata as _get_ws_meta
                _orch_cfg = _get_ws_meta(node_workspace).get("orchestrator", {})
                _followup_mode = _orch_cfg.get("followup_mode", "continuous")
                _enabled = _orch_cfg.get("enabled", True)
            except Exception:
                _followup_mode = "continuous"
                _enabled = True

            # ── Parent containers ────────────────────────────────────────
            # A task that has subtasks is a container: it is never routed to
            # a worker itself. Activate fresh containers (ready, or with the
            # orchestrator assigned by the user) and keep promoting runnable
            # subtasks of active ones; the parent completes automatically
            # when the last subtask reaches done (service-side hook).
            _container_ids = {str(t.parent_id) for t in tasks if t.parent_id}
            _container_touched = False
            for t in tasks:
                if str(t.id) not in _container_ids or not task_in_workspace(t, node_workspace):
                    continue
                try:
                    if t.assigned_agent_type == "orchestrator" and t.agent_state == AgentState.assigned:
                        # User assigned the orchestrator to the parent: don't run
                        # it — drop the queued run and process the subtasks.
                        from managers.run_manager import delete_assigned_run
                        delete_assigned_run(str(t.id))
                        promoted = tasks_service.activate_parent_container(t.id)
                        log(f"[container] task {str(t.id)[:8]} activated via orchestrator assignment — {promoted} subtask(s) promoted")
                        _container_touched = True
                    elif t.status == TaskStatus.ready and t.agent_state == AgentState.none:
                        promoted = tasks_service.activate_parent_container(t.id)
                        log(f"[container] task {str(t.id)[:8]} activated — {promoted} subtask(s) promoted")
                        _container_touched = True
                    elif t.status == TaskStatus.in_progress and t.agent_state == AgentState.none:
                        # Active container maintenance: promote subtasks whose
                        # dependencies have since completed.
                        promoted = tasks_service.promote_runnable_subtasks(t.id)
                        if promoted:
                            log(f"[container] task {str(t.id)[:8]}: {promoted} subtask(s) became runnable")
                            _container_touched = True
                except Exception as exc:
                    log(f"[warn] container handling failed for {str(t.id)[:8]}: {exc}")
            if _container_touched:
                tasks = tasks_service.list_tasks()

            def _is_container(t) -> bool:
                return str(t.id) in _container_ids

            # Tasks directly assigned to the orchestrator as a normal worker
            _direct = [
                t for t in tasks
                if t.assigned_agent_type == "orchestrator"
                and t.agent_state == AgentState.assigned
                and task_in_workspace(t, node_workspace)
                and not _is_container(t)
            ]

            # Tasks where a worker just finished — orchestrator must follow up
            # (finalise, chain next agent, assign reviewer, etc.)
            # Only included when followup_mode == "continuous".
            # Processed even when auto-orchestration is paused: followup is a
            # continuation of an already-orchestrated task, not a new assignment.
            # Containers are excluded: an in_progress parent is not a followup.
            _followup = [
                t for t in tasks
                if t.status in (TaskStatus.reviewed, TaskStatus.in_progress)
                and t.agent_state == AgentState.none
                and task_in_workspace(t, node_workspace)
                and not _is_container(t)
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
                # New tasks waiting for orchestration. Subtasks (parent_id set)
                # qualify regardless of created_by: they are promoted to ready
                # by their parent container and run like normal tasks.
                _new = [
                    t for t in tasks
                    if t.status == TaskStatus.ready
                    and t.agent_state == AgentState.none
                    and (
                        t.created_by in (CreatedBy.user, CreatedBy.external)
                        or t.parent_id is not None
                    )
                    and task_in_workspace(t, node_workspace)
                    and not _is_container(t)
                ]
                pending = _new + _followup + _direct

            for task in pending:
                # Resolve workspace / project path
                abs_ws = workspace
                if task.workspace:
                    try:
                        from workspace import resolve_project_root, project_folder_name
                        proj = getattr(task, "project", None) or None
                        if not proj:
                            pid = getattr(task, "project_id", None)
                            if pid:
                                try:
                                    from projects.storage import ProjectStore as _PS
                                    _pr = _PS(PROJECTS_FILE)
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
                log_file = run_log_path(run_id)
                try:
                    # Get or create the session for this task
                    _orch_session_id = getattr(task, "session_id", None) or None
                    if not _orch_session_id:
                        try:
                            from common.session_service import get_or_create_task_session as _goc_session
                            _orch_session_id = _goc_session(title=task.title, workspace=task.workspace, task_id=str(task.id))
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
                            "channel": "node",
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
                    # Link task to this run so agent_state reflects the live status,
                    # and assignment tools can still overwrite it when delegating.
                    tasks_service.update_task(
                        task.id,
                        status=TaskStatus.in_progress,
                        assigned_agent_type="orchestrator",
                        assigned_agent_run_id=run_id,
                    )
                    from agents.callbacks import RunStopCallback
                    from agents.agent_invoke import invoke_agent
                    from common.agent_context import current_task_id
                    from common.utils import Tee as _Tee
                    stop_cb = RunStopCallback(run_id)
                    pub_cbs = _session_publisher(_orch_session_id, run_id, "orchestrator")
                    agent = create_agent("orchestrator", workspace=abs_ws)
                    # Seed the input context so the system prompt is visible in
                    # the dashboard while the run executes (replaced at close).
                    from managers.run_manager import seed_run_input_context
                    seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", prompt)
                    _invocation = None
                    # Mark this as a tracked-task run: the orchestrator must use the
                    # assign/start task flow here, not taskless run_agent_tool.
                    # Reset after the run so the next poll iteration starts clean.
                    _task_ctx_token = current_task_id.set(str(task.id))
                    with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                        lf.write(f"[{_now()}] Orchestrator run started\n")
                        lf.write(f"[{_now()}] node_id={node_id} run_id={run_id}\n")
                        lf.write(f"[{_now()}] task_id={task.id}\n")
                        lf.write(f"[{_now()}] workspace={task.workspace or '—'}\n\n")
                        _orig_stdout, _orig_stderr = sys.stdout, sys.stderr
                        sys.stdout = _Tee(_orig_stdout, lf)
                        sys.stderr = _Tee(_orig_stderr, lf)
                        try:
                            _invocation = invoke_agent(agent, prompt, extra_callbacks=[stop_cb, *pub_cbs])
                            result = _invocation.result
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
                    current_task_id.reset(_task_ctx_token)

                    _duration_ms = _invocation.duration_ms
                    _process = _invocation.process
                    for _pub in pub_cbs:
                        _pub.publish_done(result, _invocation, stopped=stop_cb.cancelled)
                    if stop_cb.cancelled:
                        _finished_at = _utc_now_iso()
                        _update_run(
                            run_id,
                            {"status": "stopped", "finished_at": _finished_at, "exit_code": 0, "error": "stopped by user", "process": _process},
                        )
                        log(f"Task {str(task.id)[:8]} stopped by user")
                        continue
                    if result.ok:
                        _finished_at = _utc_now_iso()
                        _update_run(
                            run_id,
                            {"status": "completed", "finished_at": _finished_at, "exit_code": 0, "process": _process, "output": _result_text(result.agent_output)},
                        )
                        from tasks.context import persist_task_result
                        persist_task_result(
                            str(task.id), run_id, result.agent_output or "", agent_id="orchestrator",
                        )
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


# ── Instance mailbox ─────────────────────────────────────────────────────────

def _drain_instance_inbox(node_id: str, agent_id: str, workspace: str | None) -> bool:
    """Answer one message addressed to this node's instance, if any is waiting.

    A node is a live copy of an agent sitting in standby, so a message written
    to it on the Instances page must be answered *by this process* — with the
    tools, workspace and memory it was started with — not by a fresh subprocess
    that merely shares its agent id. The API only queues; this is the other half.

    Returns True when a message was handled, so the caller can poll again right
    away instead of sleeping through a conversation.
    """
    try:
        from instances import inbox as instance_inbox
        from instances import registry as instance_registry
        from instances import store as instance_store
        from instances.history import build_instance_history
    except Exception:
        return False

    instance = instance_store.get_by_node(node_id)
    if instance is None:
        return False
    instance_id = instance["instance_id"]
    message = instance_inbox.claim_next(instance_id)
    if message is None:
        return False

    body = str(message.get("body") or "").strip()
    msg_id = str(message.get("msg_id") or "")
    if not body:
        return True

    import os
    from uuid import uuid4
    from agents.agent_factory import create_agent
    from agents.agent_invoke import invoke_agent
    from agents.callbacks import RunStopCallback
    from chat.context import build_history_lines, history_block_lines
    from managers.run_manager import (
        _update_run, _utc_now_iso, close_run_from_result, open_run, run_log_path,
    )

    run_id = str(uuid4())
    log_file = run_log_path(run_id)
    session_id = instance.get("session_id")
    log(f"Instance message for {instance_id[:12]} → run {run_id[:8]}")

    try:
        history_lines = build_history_lines(build_instance_history(instance_id))
        prompt = "\n".join([*history_block_lines(history_lines), body])

        open_run(
            run_id, agent_id, pid=os.getpid(), session_id=session_id,
            session_type="chat", channel="instance", node_id=node_id,
            workspace=workspace, log_file=str(log_file),
            title=body[:60] + ("…" if len(body) > 60 else ""),
            input=prompt, instance_id=instance_id, message_origin="instance",
        )
        instance_registry.mark_active(instance_id, run_id, body[:200])
        instance_inbox.attach_run(msg_id, run_id)

        stop_cb = RunStopCallback(run_id)
        pub_cbs = _session_publisher(session_id, run_id, agent_id)
        agent = create_agent(agent_id, workspace=workspace)
        with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
            lf.write(f"[{_now()}] Instance message run\n")
            lf.write(f"[{_now()}] node_id={node_id} instance_id={instance_id}\n\n")
            lf.write(f"=== MESSAGE ===\n{body}\n\n=== EXECUTION ===\n")
        invocation = invoke_agent(agent, prompt, extra_callbacks=[stop_cb, *pub_cbs])
        result = invocation.result
        for pub in pub_cbs:
            pub.publish_done(result, invocation, stopped=stop_cb.cancelled)
        close_run_from_result(run_id, result, process=invocation.process)
    except Exception as exc:
        log(f"Instance message failed: {exc}")
        try:
            instance_inbox.mark_error(msg_id, str(exc))
            _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(),
                                 "exit_code": 1, "error": str(exc)})
        except Exception:
            pass
    return True


# ── Worker loop ───────────────────────────────────────────────────────────────

def run_worker_loop(node_id: str, agent_id: str, workspace: str | None) -> None:
    """Poll for tasks whose assigned_agent_type == agent_id and run them."""
    from common.workspace_context import normalize_workspace_name, task_in_workspace
    node_workspace = normalize_workspace_name(workspace) or "default"
    import os
    os.environ["AGENT_WORKSPACE"] = node_workspace

    log(f"Worker node {node_id[:8]} ({agent_id}) ready — polling every 10s")
    log(f"Worker workspace scope: {node_workspace}")
    _set_status(node_id, "running")

    POLL = 10

    while True:
        try:
            # Answer anything written to this copy before looking for tasks —
            # and before the execution-mode gate below, so a worker parked in an
            # idle workspace still replies.
            if _drain_instance_inbox(node_id, agent_id, workspace):
                continue
            from tasks import service as tasks_service
            from tasks import TaskStatus, AgentState
            from agents.agent_factory import create_agent

            # Check execution_mode — skip if the workspace uses subprocess-based execution
            try:
                from workspace import get_workspace_metadata as _get_ws_meta
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
                        from workspace import resolve_project_root, project_folder_name
                        proj = getattr(task, "project", None) or None
                        if not proj:
                            pid = getattr(task, "project_id", None)
                            if pid:
                                try:
                                    from projects.storage import ProjectStore as _PS
                                    _pr = _PS(PROJECTS_FILE)
                                    _obj = _pr.get(str(pid))
                                    if _obj:
                                        proj = project_folder_name(_obj.name)
                                except Exception:
                                    pass
                        abs_ws = str(resolve_project_root(task.workspace, proj))
                    except Exception:
                        pass

                log(f"Processing task {str(task.id)[:8]}: {task.title!r}")
                # Bind before the try so the except handler can always reference
                # it even if an import above the assignment raises.
                run_id = str(task.assigned_agent_run_id or uuid4())
                try:
                    from agents.callbacks import RunStopCallback
                    from agents.agent_invoke import invoke_agent
                    from managers.run_manager import (
                        _update_run,
                        _utc_now_iso,
                        run_log_path,
                        get_run_by_id as _get_run_rec0,
                    )
                    # Reuse the "assigned" run record created at assignment time.
                    log_file = run_log_path(run_id)
                    _worker_started_at = _utc_now_iso()
                    prompt = f"{task.title}\n\n{task.description or ''}"
                    # Preserve the run's initial channel (e.g. "external"); default
                    # to "node" for runs first materialised by this worker.
                    _existing_channel = (_get_run_rec0(run_id) or {}).get("channel") or "node"
                    _update_run(run_id, {
                        "status": "running",
                        "node_id": node_id,
                        "channel": _existing_channel,
                        "started_at": _worker_started_at,
                        "log_file": str(log_file),
                        "input": prompt,
                    })
                    # Ensure session exists and the run is linked to it
                    _session_id = None
                    try:
                        from managers.run_manager import get_run_by_id as _get_run_rec
                        from common.session_service import get_or_create_task_session, add_run_to_session
                        _run_rec = _get_run_rec(run_id) or {}
                        _session_id = _run_rec.get("session_id") or getattr(task, "session_id", None) or None
                        if not _session_id:
                            _session_id = get_or_create_task_session(title=task.title, workspace=task.workspace, task_id=str(task.id))
                            tasks_service.update_task(task.id, session_id=_session_id)
                            from managers.run_manager import _update_run as _upd
                            _upd(run_id, {"session_id": _session_id})
                        add_run_to_session(_session_id, run_id)
                    except Exception:
                        pass
                    stop_cb = RunStopCallback(run_id)
                    pub_cbs = _session_publisher(_session_id, run_id, agent_id)
                    tasks_service.update_task(task.id, status=TaskStatus.in_progress)
                    agent = create_agent(agent_id, workspace=abs_ws)
                    # Seed the input context so this worker's system prompt is
                    # visible in the dashboard while it runs (replaced at close).
                    from managers.run_manager import seed_run_input_context
                    seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", prompt)
                    from common.utils import Tee as _Tee
                    _invocation = None
                    with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                        lf.write(f"[{_now()}] Worker run started\n")
                        lf.write(f"[{_now()}] node_id={node_id} run_id={run_id}\n")
                        lf.write(f"[{_now()}] task_id={task.id}\n\n")
                        _orig_stdout, _orig_stderr = sys.stdout, sys.stderr
                        sys.stdout = _Tee(_orig_stdout, lf)
                        sys.stderr = _Tee(_orig_stderr, lf)
                        try:
                            _invocation = invoke_agent(agent, prompt, extra_callbacks=[stop_cb, *pub_cbs])
                            result = _invocation.result
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

                    _duration_ms = _invocation.duration_ms
                    _process = _invocation.process
                    for _pub in pub_cbs:
                        _pub.publish_done(result, _invocation, stopped=stop_cb.cancelled)
                    from managers.run_manager import _update_run, finalize_task_from_run
                    if stop_cb.cancelled:
                        _update_run(run_id, {"status": "stopped", "finished_at": _utc_now_iso(), "exit_code": 0, "error": "stopped by user", "process": _process})
                        log(f"Task {str(task.id)[:8]} stopped by user")
                        tasks_service.clear_agent(task.id)
                        tasks_service.stop_task(task.id)
                    elif result.ok:
                        _agent_out = _result_text(result.agent_output)
                        _update_run(run_id, {"status": "completed", "finished_at": _utc_now_iso(), "exit_code": 0, "process": _process, "output": _agent_out})
                        log(f"Task {str(task.id)[:8]} completed")
                        from tasks.context import persist_task_result
                        persist_task_result(
                            str(task.id), run_id, _agent_out, agent_id=agent_id,
                        )
                        finalize_task_from_run(run_id, "completed", 0)
                    else:
                        _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(), "exit_code": 1, "error": str(result.error or "worker error"), "process": _process})
                        log(f"Task {str(task.id)[:8]} failed: {result.error}")
                        finalize_task_from_run(run_id, "failed", 1)
                except Exception as exc:
                    log(f"Error on task {str(task.id)[:8]}: {exc}")
                    try:
                        from managers.run_manager import _update_run, finalize_task_from_run
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
            from runtime.http_server import start_http_server
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
                from runtime.http_server import start_http_server_thread
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
