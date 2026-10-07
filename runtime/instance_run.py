"""
Instance runner: the process of a resident instance (instances/carrier.py).

Started by the carrier as::

    python -m runtime.instance_run \\
        --instance-id <id> --agent-id <agent> \\
        [--workspace <abs_path>] [--log-file <path>] \\
        [--write-stdout-to-log] [--http-port <port>]

One process per instance, alive until stopped. What it listens to:

- **its mailbox**, always: the instance page, the hub's public address
  (``/api/external/{token}/messages``), a direct HTTP port when it has one.
  Each message is answered in a run of its own, with the history of the
  conversation it belongs to. Up to ``concurrency`` runs go at once, but never
  two of one conversation; the rest wait in the mailbox. The carrier is woken
  the moment a message is written (instances/wake.py) instead of polling.
  A ``turn`` message (instances/inbox.py KIND_TURN) is a whole chat request
  routed here by the backend (chat/routing.py): it runs the chat pipeline in
  this process (chat/turns.py), which is how a chat, /v1, widget or Telegram
  turn reaches a service replica. A ``job`` message (KIND_JOB) is one agent
  invocation the backend hands over, an eval case, a replay, a decomposition,
  the agent part of an entity chat (runtime/jobs.py). A replica of a runner
  service has no agent of its own (``--agent-id`` omitted) and takes turns
  and jobs only.
- **tasks**, when the instance's ``take_tasks`` input is on: tasks assigned to
  its agent in its workspace (the orchestrator agent routes new ones instead),
  one at a time, in the workspace's ``node`` execution mode. That is the
  strict resource cap a worker node used to be: the agent's tasks run only on
  copies that were started for them.

Both inputs are read from the instance row on every turn of the loop, so the
page can switch them without a restart.

Live output of every run goes to the instance's channel
(``instance:<id>``), through the backend's event endpoint like any other
out-of-process run; each run ends with an ``instance_stream_end`` event naming
its message, conversation and run. A heartbeat on the row lets another host
tell the process is alive, and carries a stop requested from a host that
cannot signal this one.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set
from uuid import uuid4

from common.paths import PROJECT_ROOT, PROJECTS_FILE

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.logging_config import marker_logger

# This process's own timestamped lines are what a Docker carrier's
# ``--write-stdout-to-log`` mirrors into the carrier log, so they go through a
# logger that emits the message only, on stdout, at INFO.
_marker_log = marker_logger(__name__)
# Diagnostics that stay out of the carrier log.
_logger = logging.getLogger(__name__ + ".debug")

HEARTBEAT_SECONDS = 15.0
TASK_SWEEP_SECONDS = 10.0
IDLE_WAIT_SECONDS = 5.0

_log_fh = None  # set in main() for Docker carriers to mirror stdout into the log file
_stopping = threading.Event()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    _marker_log.info(line)
    if _log_fh is not None:
        try:
            _log_fh.write(line + "\n")
            _log_fh.flush()
        except (OSError, ValueError):
            pass


def _result_text(value) -> str:
    txt = str(value or "").strip()
    if len(txt) > 100_000:
        txt = txt[:100_000] + "\n...[truncated]"
    return txt


def _set_status(instance_id: str, status: str, exit_code: int | None = None,
                error: str | None = None) -> None:
    try:
        from instances import carrier
        carrier.update_from_process(instance_id, status, exit_code=exit_code, error=error)
    except Exception as exc:  # noqa: BLE001 - the carrier row is best effort; the process keeps running
        log(f"[warn] Could not update the carrier status: {exc}")


def _channel_publisher(instance_id: str, run_id: str, agent_id: str,
                       meta: Optional[Dict[str, Any]] = None) -> list:
    """Live-stream one run into the instance's channel (``instance:<id>``).

    Returns a one-element callback list so callers can splat it into
    ``extra_callbacks``. The ``meta`` marker opens the live block on the
    instance page; token events only materialise when the agent was built
    with streaming on, tool and thinking events always do.
    """
    try:
        from agents.callbacks import SessionPublishCallback
        port = int(os.environ.get("DASHBOARD_PORT", "8000"))
        cb = SessionPublishCallback(f"instance:{instance_id}", run_id, agent_id, port)
        cb._post({"type": "meta", "run_id": run_id, "agent_id": agent_id,
                  "instance_id": instance_id, **(meta or {})})
        return [cb]
    except Exception as exc:  # noqa: BLE001 - a run without a live view still runs
        log(f"[warn] live stream unavailable: {exc}")
        return []


def _secrets_scope(instance: Dict[str, Any], agent_id: str, extra_names=()):
    """``extra_names``: a task's own secrets (Task.secrets), on top of the
    agent's allowlist, for that task's run only."""
    try:
        from common import secrets
        return secrets.activate(str(instance.get("workspace") or "default"), agent_id,
                                instance.get("started_by"), extra_names=extra_names)
    except Exception:  # noqa: BLE001 - no secret store: run without secrets, as before
        import contextlib
        return contextlib.nullcontext()


# ── Messages ─────────────────────────────────────────────────────────────────

_CHANNEL_BY_ORIGIN = {"external": "external", "http": "http"}


def _service_pin(service_id: Optional[str], agent_id: str) -> Optional[int]:
    """The agent version a service pins, when it names one that exists."""
    if not service_id:
        return None
    try:
        from services import store as service_store
        raw = (service_store.get(str(service_id)) or {}).get("agent_version")
        if raw is None:
            return None
        from agents import versions as agent_versions
        if agent_versions.get_version_row(agent_id, int(raw)) is None:
            _logger.warning("service %s pins version %s of %s, which is gone; answering with the live definition",
                            service_id, raw, agent_id)
            return None
        return int(raw)
    except Exception:  # noqa: BLE001 - an unreadable service answers with the live definition, logged
        _logger.warning("could not read the version pin of service %s", service_id, exc_info=True)
        return None


def answer_message(instance_id: str, agent_id: str, workspace: Optional[str],
                   message: Dict[str, Any]) -> Optional[str]:
    """Answer one claimed mailbox message in a run of its own. Returns the run id.

    The conversation's prior turns travel as history next to the message, so
    the prompt is the message itself and nothing else.
    """
    from instances import inbox as instance_inbox
    from instances import registry as instance_registry
    from instances import store as instance_store
    from instances.history import build_instance_history

    body = str(message.get("body") or "").strip()
    msg_id = str(message.get("msg_id") or "")
    conversation_id = message.get("conversation_id")
    if str(message.get("kind") or "") == instance_inbox.KIND_TURN:
        from chat import turns as chat_turns
        return chat_turns.execute_turn(instance_id, workspace, message)
    if str(message.get("kind") or "") == instance_inbox.KIND_JOB:
        from runtime import jobs as runner_jobs
        return runner_jobs.execute_job(instance_id, workspace, message)
    instance = instance_store.get(instance_id) or {}
    if not agent_id:
        log(f"Message {msg_id[:12]} dropped: this runner has no agent of its own")
        instance_inbox.mark_error(msg_id, "this instance runs no agent of its own; it takes chat turns only")
        return None
    if not body:
        return None
    service_id = instance.get("service_id") or None
    # The service's version pin (services/store.py ``agent_version``): a
    # plain message to a pinned service is answered by that version, the
    # same as a chat turn routed to it (chat/turns.py).
    pin = _service_pin(service_id, agent_id)

    from agents.agent_factory import create_agent
    from agents.agent_invoke import invoke_agent
    from agents.callbacks import RunStopCallback
    from chat.context import build_history_messages
    from managers.run_manager import (
        _update_run, _utc_now_iso, close_run_from_result, open_run, run_log_path,
    )

    run_id = str(uuid4())
    log_file = run_log_path(run_id)
    origin = str(message.get("origin") or "web")
    public_cid = instance_inbox.public_conversation(conversation_id)
    log(f"Message {msg_id[:12]} ({public_cid}) → run {run_id[:8]}")
    pub_cbs: list = []
    try:
        history = build_history_messages(
            build_instance_history(instance_id, conversation_id=conversation_id,
                                   service_id=service_id))
        open_run(
            run_id, agent_id, pid=None, session_id=instance.get("session_id"),
            session_type="chat", channel=_CHANNEL_BY_ORIGIN.get(origin, "instance"),
            workspace=instance.get("workspace"), log_file=str(log_file),
            title=body[:60] + ("…" if len(body) > 60 else ""),
            input=body, instance_id=instance_id, message_origin=origin,
            conversation_id=conversation_id, carrier_run=True, inbox_msg_id=msg_id,
            **({"service_id": service_id} if service_id else {}),
            agent_version_pin=pin,
        )
        instance_inbox.attach_run(msg_id, run_id)
        instance_registry.mark_active(instance_id, run_id, body[:200])
        pub_cbs = _channel_publisher(instance_id, run_id, agent_id,
                                     {"msg_id": msg_id, "conversation_id": public_cid})
        stop_cb = RunStopCallback(run_id)
        with _secrets_scope(instance, agent_id):
            agent = create_agent(agent_id, workspace=workspace,
                                 **({"definition_version": pin} if pin is not None else {}))
            with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                lf.write(f"[{_now()}] Instance message run\n")
                lf.write(f"[{_now()}] instance_id={instance_id} msg_id={msg_id} "
                         f"conversation={public_cid}\n\n")
                lf.write(f"=== MESSAGE ===\n{body}\n\n=== EXECUTION ===\n")
            invocation = invoke_agent(agent, body, history=history,
                                      extra_callbacks=[stop_cb, *pub_cbs])
        result = invocation.result
        for pub in pub_cbs:
            pub.publish_done(result, invocation, stopped=stop_cb.cancelled)
        if stop_cb.cancelled:
            from managers.run_manager import close_run
            close_run(run_id, status="stopped", exit_code=0, error="stopped by user",
                      process=invocation.process)
        else:
            close_run_from_result(run_id, result, process=invocation.process)
    except Exception as exc:  # noqa: BLE001 - the message is marked failed below; the loop keeps serving
        log(f"Message {msg_id[:12]} failed: {exc}")
        try:
            instance_inbox.mark_error(msg_id, str(exc))
            _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(),
                                 "exit_code": 1, "error": str(exc)})
        except Exception:  # noqa: BLE001 - already failing; the message stays as delivered
            _logger.debug("could not mark message %s failed", msg_id, exc_info=True)
        for pub in pub_cbs:
            try:
                pub._post({"type": "done", "ok": False, "error": str(exc), "run_id": run_id})
            except Exception:  # noqa: BLE001 - a viewer that went away
                _logger.debug("could not post the failure of run %s", run_id, exc_info=True)
    finally:
        for pub in pub_cbs:
            try:
                pub._post({"type": "instance_stream_end", "run_id": run_id, "msg_id": msg_id,
                           "conversation_id": public_cid})
            except Exception:  # noqa: BLE001 - a viewer that went away
                _logger.debug("could not post the stream end of run %s", run_id, exc_info=True)
    return run_id


# ── Tasks: worker ────────────────────────────────────────────────────────────

def _task_workspace_path(task, workspace: Optional[str]) -> Optional[str]:
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
                        _obj = _PS(PROJECTS_FILE).get(str(pid))
                        if _obj:
                            proj = project_folder_name(_obj.name)
                    except Exception:  # noqa: BLE001 - no project folder: the workspace root is used
                        _logger.debug("could not resolve the project folder", exc_info=True)
            abs_ws = str(resolve_project_root(task.workspace, proj))
        except Exception:  # noqa: BLE001 - the workspace root is used
            _logger.debug("could not resolve the task workspace", exc_info=True)
    return abs_ws


def _task_build_pin(task: Any, run_id: str, agent_id: str) -> Dict[str, Any]:
    """``create_agent`` keywords for a task run on a resident worker: the
    version pin (the launch params' own, else the task's) and the per-run
    overrides of the launch params (agents/run_overrides.py). The run record
    gets the version and the overrides the same way a subprocess run does."""
    from managers.run_manager import _update_run
    params = dict(getattr(task, "assigned_agent_params", None) or {})
    out: Dict[str, Any] = {}
    record: Dict[str, Any] = {}
    raw_pin = params.get("agent_version")
    if raw_pin is None:
        raw_pin = getattr(task, "agent_version", None)
    if raw_pin is not None:
        try:
            pin = int(raw_pin)
            from agents import versions as agent_versions
            if agent_versions.get_version_row(agent_id, pin) is not None:
                out["definition_version"] = pin
                record["agent_version"] = pin
            else:
                _logger.warning("task %s pins version %s of %s, which is gone; building the live definition",
                                getattr(task, "id", "?"), pin, agent_id)
        except (TypeError, ValueError):
            _logger.debug("task %s has an unreadable version pin", getattr(task, "id", "?"), exc_info=True)
    if any(params.get(k) not in (None, "", {}) for k in ("overrides", "tool_policy", "output_schema")):
        from agents import run_overrides
        overrides = run_overrides.fold_legacy(params.get("overrides"),
                                              tool_policy=params.get("tool_policy"),
                                              output_schema=params.get("output_schema"))
        if overrides:
            out.update(run_overrides.build_kwargs(overrides))
            record["overrides"] = run_overrides.record_view(overrides)
    if record:
        try:
            _update_run(run_id, record)
        except Exception:  # noqa: BLE001 - the record is bookkeeping; the run builds as pinned regardless
            _logger.debug("could not stamp the pin on run %s", run_id, exc_info=True)
    return out


def sweep_worker_tasks(instance_id: str, agent_id: str, workspace: Optional[str],
                       scope: str) -> int:
    """Run every task assigned to this agent in the workspace, one at a time.
    Returns how many were picked up."""
    from common.workspace_context import task_in_workspace
    from tasks import service as tasks_service
    from tasks import TaskStatus, AgentState
    from agents.agent_factory import create_agent

    # Tasks of a workspace in "subprocess" mode are launched as processes of
    # their own; taking them here too would run them twice.
    try:
        from workspace import get_workspace_metadata as _get_ws_meta
        mode = _get_ws_meta(scope).get("orchestrator", {}).get("execution_mode", "subprocess")
    except Exception:  # noqa: BLE001 - no workspace metadata: the default mode applies
        _logger.debug("could not read the orchestrator mode of %s", scope, exc_info=True)
        mode = "subprocess"
    if mode != "node":
        return 0

    assigned = [
        t for t in tasks_service.list_tasks()
        if t.assigned_agent_type == agent_id
        and t.agent_state == AgentState.assigned
        and task_in_workspace(t, scope)
    ]
    assigned = tasks_service.order_for_dispatch(assigned)
    picked = 0
    for task in assigned:
        if _stopping.is_set():
            break
        picked += 1
        abs_ws = _task_workspace_path(task, workspace)
        log(f"Processing task {str(task.id)[:8]}: {task.title!r}")
        run_id = str(task.assigned_agent_run_id or uuid4())
        try:
            from agents.callbacks import RunStopCallback
            from agents.agent_invoke import invoke_agent
            from managers.run_manager import (
                _update_run, _utc_now_iso, run_log_path, get_run_by_id,
                finalize_task_from_run, seed_run_input_context,
            )
            log_file = run_log_path(run_id)
            prompt = f"{task.title}\n\n{task.description or ''}"
            # Keep the run's first channel (e.g. "external"); "instance" otherwise.
            channel = (get_run_by_id(run_id) or {}).get("channel") or "instance"
            _update_run(run_id, {
                "status": "running", "instance_id": instance_id, "carrier_run": True,
                "channel": channel, "started_at": _utc_now_iso(),
                "log_file": str(log_file), "input": prompt,
            })
            session_id = None
            try:
                from common.session_service import get_or_create_task_session, add_run_to_session
                session_id = ((get_run_by_id(run_id) or {}).get("session_id")
                              or getattr(task, "session_id", None) or None)
                if not session_id:
                    session_id = get_or_create_task_session(
                        title=task.title, workspace=task.workspace, task_id=str(task.id))
                    tasks_service.update_task(task.id, session_id=session_id)
                    _update_run(run_id, {"session_id": session_id})
                add_run_to_session(session_id, run_id)
            except Exception:  # noqa: BLE001 - the run executes without a session link
                _logger.debug("could not link run %s to its session", run_id, exc_info=True)
            stop_cb = RunStopCallback(run_id)
            pub_cbs = _channel_publisher(instance_id, run_id, agent_id,
                                         {"task_id": str(task.id)})
            tasks_service.update_task(task.id, status=TaskStatus.in_progress)
            from instances import store as instance_store
            instance = instance_store.get(instance_id) or {"workspace": scope}
            from common.utils import Tee as _Tee
            # A deployment's resources for this task's runs only: its memory
            # pools instead of the agent's own binding, and its extra secret
            # names (docs/deployments.md, "Resources").
            _task_secrets = [str(n).strip() for n in (getattr(task, "secrets", None) or []) if str(n or "").strip()]
            _task_pools = [str(p).strip() for p in (getattr(task, "memory_pool_ids", None) or []) if str(p or "").strip()]
            _build_kwargs: Dict[str, Any] = {}
            if _task_pools:
                _build_kwargs["memory_pool"] = _task_pools
                _build_kwargs["memory_access"] = str(getattr(task, "memory_access", None) or "write")
            if _task_secrets:
                _build_kwargs["extra_secrets"] = _task_secrets
            # The version the task (or its launch params) pins and the run's
            # overrides, as a subprocess launch would build them
            # (agents/agent_launcher.py): a pinned task must not quietly run
            # the live definition because a resident worker took it.
            _build_kwargs.update(_task_build_pin(task, run_id, agent_id))
            with _secrets_scope(instance, agent_id, _task_secrets):
                agent = create_agent(agent_id, workspace=abs_ws, **_build_kwargs)
                seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", prompt)
                with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                    lf.write(f"[{_now()}] Worker run started\n")
                    lf.write(f"[{_now()}] instance_id={instance_id} run_id={run_id}\n")
                    lf.write(f"[{_now()}] task_id={task.id}\n\n")
                    orig_out, orig_err = sys.stdout, sys.stderr
                    sys.stdout, sys.stderr = _Tee(orig_out, lf), _Tee(orig_err, lf)
                    try:
                        invocation = invoke_agent(agent, prompt, extra_callbacks=[stop_cb, *pub_cbs])
                        result = invocation.result
                        if stop_cb.cancelled:
                            lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                        elif result.ok:
                            lf.write("\nAgent output:\n" + _result_text(result.agent_output) + "\n")
                        else:
                            lf.write(f"\nAgent failed: {result.error}\n")
                    finally:
                        sys.stdout, sys.stderr = orig_out, orig_err
            process = invocation.process
            for pub in pub_cbs:
                pub.publish_done(result, invocation, stopped=stop_cb.cancelled)
                pub._post({"type": "instance_stream_end", "run_id": run_id, "task_id": str(task.id)})
            if stop_cb.cancelled:
                _update_run(run_id, {"status": "stopped", "finished_at": _utc_now_iso(), "exit_code": 0,
                                     "error": "stopped by user", "process": process})
                log(f"Task {str(task.id)[:8]} stopped by user")
                tasks_service.clear_agent(task.id)
                tasks_service.stop_task(task.id)
            elif result.ok:
                out = _result_text(result.agent_output)
                _update_run(run_id, {"status": "completed", "finished_at": _utc_now_iso(),
                                     "exit_code": 0, "process": process, "output": out})
                log(f"Task {str(task.id)[:8]} completed")
                from tasks.context import persist_task_result
                persist_task_result(str(task.id), run_id, out, agent_id=agent_id)
                finalize_task_from_run(run_id, "completed", 0)
            else:
                _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(), "exit_code": 1,
                                     "error": str(result.error or "worker error"), "process": process})
                log(f"Task {str(task.id)[:8]} failed: {result.error}")
                finalize_task_from_run(run_id, "failed", 1)
        except Exception as exc:  # noqa: BLE001 - the task is marked failed below; the sweep continues
            log(f"Error on task {str(task.id)[:8]}: {exc}")
            try:
                from managers.run_manager import _update_run, _utc_now_iso, finalize_task_from_run
                _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(),
                                     "exit_code": 1, "error": str(exc)})
                finalize_task_from_run(run_id, "failed", 1)
            except Exception:  # noqa: BLE001 - already failing; the next sweep sees the task
                _logger.debug("could not mark run %s failed", run_id, exc_info=True)
    return picked


# ── Tasks: orchestrator ──────────────────────────────────────────────────────

def sweep_orchestrator_tasks(instance_id: str, workspace: Optional[str], scope: str) -> int:
    """One orchestration pass over the workspace's tasks. Returns how many
    tasks the orchestrator ran on."""
    from common.workspace_context import task_in_workspace
    from tasks import service as tasks_service
    from tasks import TaskStatus, AgentState, CreatedBy
    from agents.agent_factory import create_agent
    from managers.run_manager import _upsert_run, _update_run, _utc_now_iso, run_log_path

    tasks = tasks_service.list_tasks()
    try:
        from workspace import get_workspace_metadata as _get_ws_meta
        orch_cfg = _get_ws_meta(scope).get("orchestrator", {})
        followup_mode = orch_cfg.get("followup_mode", "continuous")
        enabled = orch_cfg.get("enabled", True)
    except Exception:  # noqa: BLE001 - no workspace metadata: the defaults apply
        _logger.debug("could not read the orchestrator settings of %s", scope, exc_info=True)
        followup_mode, enabled = "continuous", True

    # A task that has subtasks is a container: never routed to a worker
    # itself. Activate fresh containers and keep promoting runnable subtasks
    # of active ones; the parent completes when its last subtask does.
    container_ids = {str(t.parent_id) for t in tasks if t.parent_id}
    touched = False
    for t in tasks:
        if str(t.id) not in container_ids or not task_in_workspace(t, scope):
            continue
        try:
            if t.assigned_agent_type == "orchestrator" and t.agent_state == AgentState.assigned:
                from managers.run_manager import delete_assigned_run
                delete_assigned_run(str(t.id))
                promoted = tasks_service.activate_parent_container(t.id)
                log(f"[container] task {str(t.id)[:8]} activated via orchestrator assignment, "
                    f"{promoted} subtask(s) promoted")
                touched = True
            elif t.status == TaskStatus.ready and t.agent_state == AgentState.none:
                promoted = tasks_service.activate_parent_container(t.id)
                log(f"[container] task {str(t.id)[:8]} activated, {promoted} subtask(s) promoted")
                touched = True
            elif t.status == TaskStatus.in_progress and t.agent_state == AgentState.none:
                promoted = tasks_service.promote_runnable_subtasks(t.id)
                if promoted:
                    log(f"[container] task {str(t.id)[:8]}: {promoted} subtask(s) became runnable")
                    touched = True
        except Exception as exc:  # noqa: BLE001 - one task's container handling does not stop the sweep
            log(f"[warn] container handling failed for {str(t.id)[:8]}: {exc}")
    if touched:
        tasks = tasks_service.list_tasks()

    def _is_container(t) -> bool:
        return str(t.id) in container_ids

    direct = [t for t in tasks
              if t.assigned_agent_type == "orchestrator" and t.agent_state == AgentState.assigned
              and task_in_workspace(t, scope) and not _is_container(t)]
    # A worker just finished: the orchestrator follows up (finalise, chain the
    # next agent, assign a reviewer), even while auto-orchestration is paused.
    followup = [t for t in tasks
                if t.status in (TaskStatus.reviewed, TaskStatus.in_progress)
                and t.agent_state == AgentState.none
                and task_in_workspace(t, scope) and not _is_container(t)
                ] if followup_mode == "continuous" else []
    if enabled:
        new = [t for t in tasks
               if t.status == TaskStatus.ready and t.agent_state == AgentState.none
               and (t.created_by in (CreatedBy.user, CreatedBy.external) or t.parent_id is not None)
               and task_in_workspace(t, scope) and not _is_container(t)]
        pending = new + followup + direct
    else:
        pending = direct + followup
    pending = tasks_service.order_for_dispatch(pending)

    for task in pending:
        if _stopping.is_set():
            break
        abs_ws = _task_workspace_path(task, workspace)
        is_followup = task.status in (TaskStatus.reviewed, TaskStatus.in_progress)
        log(f"{'[follow-up]' if is_followup else '[new]'} task {str(task.id)[:8]}: "
            f"{task.title!r} (status={task.status})")
        run_id = str(uuid4())
        log_file = run_log_path(run_id)
        try:
            session_id = getattr(task, "session_id", None) or None
            if not session_id:
                try:
                    from common.session_service import get_or_create_task_session
                    session_id = get_or_create_task_session(
                        title=task.title, workspace=task.workspace, task_id=str(task.id))
                    tasks_service.update_task(task.id, session_id=session_id)
                except Exception:  # noqa: BLE001 - the run executes without a session
                    _logger.debug("could not create a session for task %s", task.id, exc_info=True)
                    session_id = None
            try:
                from common.session_service import add_run_to_session
                if session_id:
                    add_run_to_session(session_id, run_id)
            except Exception:  # noqa: BLE001 - the run executes without a session link
                _logger.debug("could not link run %s to its session", run_id, exc_info=True)
            if is_followup:
                prompt = (f"[MONITOR] Task ID: {task.id}\nTitle: {task.title}\nStatus: {task.status}\n\n"
                          "A previously started agent has finished working on this task. "
                          "Skip Steps 1-4. Go directly to Step 5 of your instructions.")
            else:
                prompt = (f"Task ID: {task.id}\nTitle: {task.title}\n"
                          f"Description: {task.description or 'No description'}\n\n"
                          "Analyse this task and coordinate its execution by assigning "
                          "appropriate specialized agents.")
            _upsert_run({
                "run_id": run_id, "task_id": str(task.id), "agent_id": "orchestrator",
                "instance_id": instance_id, "carrier_run": True, "channel": "instance",
                "pid": None, "status": "running", "session_type": "task",
                "session_id": session_id, "started_at": _utc_now_iso(), "finished_at": None,
                "exit_code": None, "error": None, "log_file": str(log_file), "input": prompt,
            })
            tasks_service.update_task(
                task.id, status=TaskStatus.in_progress,
                executor={"kind": "agent", "id": "orchestrator"},
                assigned_agent_type="orchestrator", assigned_agent_run_id=run_id,
            )
            from agents.callbacks import RunStopCallback
            from agents.agent_invoke import invoke_agent
            from common.agent_context import current_task_id
            from common.utils import Tee as _Tee
            from managers.run_manager import seed_run_input_context
            stop_cb = RunStopCallback(run_id)
            pub_cbs = _channel_publisher(instance_id, run_id, "orchestrator", {"task_id": str(task.id)})
            agent = create_agent("orchestrator", workspace=abs_ws)
            seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", prompt)
            # A tracked-task run: the orchestrator must use the assign/start
            # task flow here, not taskless run_agent_tool.
            ctx_token = current_task_id.set(str(task.id))
            try:
                with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                    lf.write(f"[{_now()}] Orchestrator run started\n")
                    lf.write(f"[{_now()}] instance_id={instance_id} run_id={run_id}\n")
                    lf.write(f"[{_now()}] task_id={task.id}\n")
                    lf.write(f"[{_now()}] workspace={task.workspace or '-'}\n\n")
                    orig_out, orig_err = sys.stdout, sys.stderr
                    sys.stdout, sys.stderr = _Tee(orig_out, lf), _Tee(orig_err, lf)
                    try:
                        invocation = invoke_agent(agent, prompt, extra_callbacks=[stop_cb, *pub_cbs])
                        result = invocation.result
                        if stop_cb.cancelled:
                            lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                        elif result.ok:
                            lf.write("\nAgent output:\n" + _result_text(result.agent_output) + "\n")
                        else:
                            lf.write(f"\nAgent failed: {result.error}\n")
                    finally:
                        sys.stdout, sys.stderr = orig_out, orig_err
            finally:
                current_task_id.reset(ctx_token)
            process = invocation.process
            for pub in pub_cbs:
                pub.publish_done(result, invocation, stopped=stop_cb.cancelled)
                pub._post({"type": "instance_stream_end", "run_id": run_id, "task_id": str(task.id)})
            if stop_cb.cancelled:
                _update_run(run_id, {"status": "stopped", "finished_at": _utc_now_iso(), "exit_code": 0,
                                     "error": "stopped by user", "process": process})
                log(f"Task {str(task.id)[:8]} stopped by user")
                continue
            if result.ok:
                _update_run(run_id, {"status": "completed", "finished_at": _utc_now_iso(), "exit_code": 0,
                                     "process": process, "output": _result_text(result.agent_output)})
                from tasks.context import persist_task_result
                persist_task_result(str(task.id), run_id, result.agent_output or "", agent_id="orchestrator")
                latest = tasks_service.get_task(task.id)
                delegated = bool(latest and latest.assigned_agent_type
                                 and latest.assigned_agent_type != "orchestrator")
                # No delegation: return the task to the queue, unless the user
                # moved it away while the orchestrator was running.
                if not delegated:
                    if latest and latest.assigned_agent_type == "orchestrator":
                        tasks_service.clear_agent(task.id)
                    if latest and latest.status == TaskStatus.in_progress:
                        if is_followup:
                            # Safety net: Step 5 should have resolved it; do it
                            # here so it cannot loop through the follow-up filter.
                            tasks_service.update_task(task.id, status=TaskStatus.resolved)
                        elif latest.agent_state == AgentState.none:
                            # A worker finished during this run and already set
                            # the follow-up signal; leave it for the next pass.
                            pass
                        else:
                            tasks_service.update_task(task.id, status=TaskStatus.ready)
                log(f"Task {str(task.id)[:8]} orchestrated successfully")
            else:
                _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(), "exit_code": 1,
                                     "error": str(result.error or "orchestrator error"), "process": process})
                log(f"Task {str(task.id)[:8]} orchestration error: {result.error}")
                latest = tasks_service.get_task(task.id)
                if latest and latest.assigned_agent_type == "orchestrator":
                    tasks_service.clear_agent(task.id)
                tasks_service.block_task(task.id, reason=result.error or "orchestrator error")
        except Exception as exc:  # noqa: BLE001 - the task is blocked below; the sweep continues
            log(f"Error on task {str(task.id)[:8]}: {exc}")
            try:
                _update_run(run_id, {"status": "failed", "finished_at": _utc_now_iso(),
                                     "exit_code": 1, "error": str(exc)})
            except Exception:  # noqa: BLE001 - already failing; the next sweep sees the task
                _logger.debug("could not mark run %s failed", run_id, exc_info=True)
            try:
                latest = tasks_service.get_task(task.id)
                if latest and latest.assigned_agent_type == "orchestrator":
                    tasks_service.clear_agent(task.id)
            except Exception:  # noqa: BLE001 - already failing; the task keeps its agent
                _logger.debug("could not clear the agent of task %s", task.id, exc_info=True)
    return len(pending)


# ── The loop ─────────────────────────────────────────────────────────────────

class InstanceLoop:
    """Mailbox, task input and heartbeat of one resident instance."""

    def __init__(self, instance_id: str, agent_id: Optional[str], workspace: Optional[str]):
        from common.workspace_context import normalize_workspace_name
        from instances import carrier

        self.instance_id = instance_id
        # Empty for a runner: a replica bound to no agent, which takes chat
        # turns (each naming its own agent) and never tasks.
        self.agent_id = (agent_id or "").strip()
        self.workspace = workspace
        self.scope = normalize_workspace_name(workspace) or "default"
        self.pool = ThreadPoolExecutor(max_workers=carrier.MAX_CONCURRENCY,
                                       thread_name_prefix="instance-run")
        self.busy: Dict[Future, Optional[str]] = {}
        self.lock = threading.Lock()
        self.task_thread: Optional[threading.Thread] = None
        self.next_sweep = 0.0

    # Conversations answering right now (stored form; None is the main one).
    def busy_conversations(self) -> Set[Optional[str]]:
        with self.lock:
            done = [f for f in self.busy if f.done()]
            for f in done:
                self.busy.pop(f, None)
            return set(self.busy.values())

    def running(self) -> int:
        self.busy_conversations()
        with self.lock:
            return len(self.busy)

    def _after_run(self, future: Future) -> None:
        with self.lock:
            self.busy.pop(future, None)
            others = len(self.busy)
        try:
            exc = future.exception()
            if exc:
                log(f"run thread failed: {exc}")
        except Exception:  # noqa: BLE001 - reporting only
            _logger.debug("could not read the result of a run thread", exc_info=True)
        # The finished run moved the instance to standby; another run of a
        # different conversation may still be going.
        if others or (self.task_thread and self.task_thread.is_alive()):
            try:
                from instances import registry as instance_registry
                instance_registry.mark_active(self.instance_id, None, None)
            except Exception:  # noqa: BLE001 - the state label is cosmetic; the next run fixes it
                _logger.debug("could not mark %s active", self.instance_id, exc_info=True)

    def claim_messages(self, concurrency: int) -> int:
        from instances import inbox as instance_inbox

        started = 0
        while not _stopping.is_set():
            busy = self.busy_conversations()
            if self.running() >= concurrency:
                break
            message = instance_inbox.claim_next(self.instance_id, exclude_conversations=busy)
            if message is None:
                break
            cid = message.get("conversation_id")
            future = self.pool.submit(answer_message, self.instance_id, self.agent_id,
                                      self.workspace, message)
            with self.lock:
                self.busy[future] = cid
            future.add_done_callback(self._after_run)
            started += 1
        return started

    def maybe_sweep_tasks(self, instance: Dict[str, Any]) -> None:
        if not instance.get("take_tasks") or not self.agent_id:
            return
        if self.task_thread and self.task_thread.is_alive():
            return
        if time.monotonic() < self.next_sweep:
            return
        self.next_sweep = time.monotonic() + TASK_SWEEP_SECONDS

        def _sweep():
            try:
                if self.agent_id == "orchestrator":
                    sweep_orchestrator_tasks(self.instance_id, self.workspace, self.scope)
                else:
                    sweep_worker_tasks(self.instance_id, self.agent_id, self.workspace, self.scope)
            except Exception as exc:  # noqa: BLE001 - the sweep retries on the next tick
                log(f"Task sweep error: {exc}\n{traceback.format_exc()}")

        self.task_thread = threading.Thread(target=_sweep, name="instance-tasks", daemon=True)
        self.task_thread.start()

    def run(self) -> None:
        from instances import carrier, store, wake

        log(f"Instance {self.instance_id} ({self.agent_id or 'runner'}) ready, workspace scope {self.scope}")
        os.environ["AGENT_WORKSPACE"] = self.scope
        _set_status(self.instance_id, "running")
        heartbeat = threading.Thread(target=self._heartbeat, name="instance-heartbeat", daemon=True)
        heartbeat.start()
        while not _stopping.is_set():
            try:
                instance = store.get(self.instance_id) or {}
                concurrency = carrier.clamp_concurrency(instance.get("concurrency") or 1)
                self.claim_messages(concurrency)
                self.maybe_sweep_tasks(instance)
                timeout = IDLE_WAIT_SECONDS
                if instance.get("take_tasks"):
                    timeout = max(0.2, min(timeout, self.next_sweep - time.monotonic()))
                if self.running() >= concurrency:
                    # Every slot is taken: nothing to claim until one frees.
                    time.sleep(min(timeout, wake.poll_seconds()))
                else:
                    wake.wait(self.instance_id, timeout)
            except Exception as exc:  # noqa: BLE001 - the loop survives one bad iteration
                log(f"Loop error: {exc}\n{traceback.format_exc()}")
                time.sleep(1.0)

    def _heartbeat(self) -> None:
        from instances import carrier, store
        while not _stopping.wait(HEARTBEAT_SECONDS):
            carrier.heartbeat(self.instance_id, **_build_counts())
            try:
                inst = store.get(self.instance_id) or {}
            except Exception:  # noqa: BLE001 - a transient read; the next heartbeat retries
                _logger.debug("could not read instance %s", self.instance_id, exc_info=True)
                continue
            if inst.get("stop_requested_at"):
                # Stop asked from a host that cannot signal this one.
                log("Stop requested through the instance record")
                os.kill(os.getpid(), signal.SIGTERM)
                return


def _build_counts() -> Dict[str, Any]:
    """This replica's agent build reuse (agents/agent_cache.py), sent with
    every heartbeat: the builds live in this process's memory, so this is
    the only way the hub's health snapshot sees them (common/health.py)."""
    try:
        from agents.agent_cache import cache_stats
        return {"agent_builds": cache_stats()}
    except Exception:  # noqa: BLE001 - the beat goes out without the counts
        _logger.debug("agent build counts unavailable", exc_info=True)
        return {}


# ── Entry point ──────────────────────────────────────────────────────────────

def _on_sigterm(instance_id: str):
    def handler(signum, frame):
        if _stopping.is_set():
            return
        _stopping.set()
        log("SIGTERM received, stopping")
        _set_status(instance_id, "stopped", exit_code=0)
        try:
            from instances import store
            store.update(instance_id, stop_requested_at=None)
        except Exception:  # noqa: BLE001 - exiting anyway
            _logger.debug("could not clear the stop request of %s", instance_id, exc_info=True)
        os._exit(0)
    return handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Resident instance runner")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--agent-id", default="",
                        help="The agent this instance is a copy of; omitted for a runner")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--write-stdout-to-log", action="store_true",
                        help="Mirror stdout into --log-file (used in Docker)")
    parser.add_argument("--http-port", type=int, default=None,
                        help="Also answer on this port directly (runtime/http_server.py)")
    args = parser.parse_args()

    if args.write_stdout_to_log and args.log_file:
        global _log_fh
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_fh = open(log_path, "a", encoding="utf-8", buffering=1)

    os.environ.setdefault("AGENT_INSTANCE_ID", args.instance_id)
    log(f"=== Instance {args.instance_id} starting, agent={args.agent_id or 'runner'} ===")
    signal.signal(signal.SIGTERM, _on_sigterm(args.instance_id))

    if args.http_port and args.agent_id:
        try:
            from runtime.http_server import start_http_server_thread
            start_http_server_thread(args.instance_id, args.agent_id, args.http_port,
                                     workspace=args.workspace, log_file=args.log_file)
            log(f"Direct HTTP port {args.http_port} open")
        except Exception as exc:  # noqa: BLE001 - the instance serves its inbox without the direct port
            log(f"[warn] Could not start the HTTP server: {exc}")

    try:
        InstanceLoop(args.instance_id, args.agent_id, args.workspace).run()
    except KeyboardInterrupt:
        log("Interrupted")
        _set_status(args.instance_id, "stopped", exit_code=0)
    except Exception as exc:  # noqa: BLE001 - reported below as the carrier's failure
        log(f"Fatal: {exc}\n{traceback.format_exc()}")
        _set_status(args.instance_id, "failed", exit_code=1, error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
