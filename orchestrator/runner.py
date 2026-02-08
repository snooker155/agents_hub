#!/usr/bin/env python3
"""
Long-running orchestrator daemon.

- Initializes access to the file-based tasks store.
- Periodically polls for new top-level tasks and processes them via the
  Orchestrator Agent (LLM-powered) to decompose into subtasks and plan execution.
- Minimal configuration via environment variables:
    ORCH_POLL_INTERVAL: seconds between polls (float, default 5.0)
    ORCH_LOG_LEVEL: logging level (DEBUG, INFO, WARNING, ERROR) default INFO
- Cleanly terminates on SIGINT/SIGTERM.

Note:
- Our Task model uses status "todo" for newly created tasks. The daemon treats
  those as "new" tasks.
- If OPENAI_API_KEY is not set, the agent cannot run; such tasks are marked as
  blocked with a clear reason.

Run:
  python -m orchestrator.runner

If installed with a console entrypoint, the command may be available as
  orchestratord
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Optional
from uuid import UUID

from .tasks_service import (
    list_tasks as svc_list_tasks,
    update_task as svc_update_task,
)

from tasks import Task, TaskStatus, CreatedBy, AgentState
from .config import get_settings, require_openai_key
from .agents.run_manager import get_status as get_agent_run_status


# Lazy import agent pieces to avoid import costs when not needed

def _try_run_agent_for_task(task: Task) -> tuple[bool, Optional[str]]:
    """Try to run the Orchestrator Agent to process a task.

    Returns (ok, error_message). If ok is True, the agent completed without raising.
    If ok is False, error_message may contain the reason.
    """
    try:
        # Ensure we have OPENAI key early to provide friendly message
        st = get_settings()
        require_openai_key(st)
    except Exception as e:
        return False, f"Agent prerequisites missing: {e}"

    # Import here to avoid importing langchain stack unless we can run
    try:
        from .agent import run_decomposing_agent
    except Exception as e:
        return False, f"Agent import failed: {e}"

    try:
        _ = run_decomposing_agent(str(task.id), task.title, task.description, verbose=False)
        return True, None
    except Exception as e:
        return False, f"Agent execution failed: {e}"


def _env_float(name: str, default: float) -> float:
    st = get_settings().model_dump()
    v = st[name.lower()]
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _env_level(name: str, default: str = "INFO") -> int:
    st = get_settings().model_dump()
    v = (st[name.lower()] or default).upper()
    return getattr(logging, v, logging.INFO)


def _configure_logging() -> None:
    lvl = _env_level("ORCH_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)s [orchestratord] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _should_pick(t: Task) -> bool:
    # Consider only top-level user tasks in "todo" status as new items
    if t.parent_id is not None:
        return False
    if t.created_by != CreatedBy.user:
        return False
    # Only decompose if explicitly requested
    if not t.should_decompose:
        return False
    return t.status == TaskStatus.todo


def _process_task(t: Task) -> None:
    log = logging.getLogger("orchestratord")
    log.info(f"Picked task {t.id} '{t.title}' for planning")

    # Mark as in_progress to avoid re-picking
    svc_update_task(t.id, status=TaskStatus.in_progress)

    ok, err = _try_run_agent_for_task(t)
    if ok:
        # Mark as done; subtasks were created by the agent
        svc_update_task(t.id, status=TaskStatus.done)
        log.info(f"Task {t.id} planned successfully")
    else:
        # Block the task with reason
        msg = err or "unknown error"
        svc_update_task(t.id, status=TaskStatus.blocked, blocked_reason=msg)
        log.error(f"Task {t.id} planning failed: {msg}")


def _reconcile_agent_states() -> None:
    """Check running agents and update task status if they finished."""
    log = logging.getLogger("orchestratord")
    try:
        # Get all tasks - simpler than filtering in service for now
        tasks = svc_list_tasks()
        running_tasks = [t for t in tasks if t.agent_state == AgentState.running]
        
        for t in running_tasks:
            # Check actual run status
            status_info = get_agent_run_status(str(t.id))
            if not status_info:
                # No run info found?
                # If it's been running for long without run info, maybe it crashed?
                # For now, ignore or log warning.
                continue

            run_status = status_info.get("status")  # running|stop|completed|stopped|failed|error
            
            if run_status in ("completed", "done", "finished"):
                log.info(f"Agent for task {t.id} completed. Updating task status.")
                svc_update_task(
                    t.id, 
                    agent_state=AgentState.completed, 
                    status=TaskStatus.done
                )
            elif run_status in ("failed", "error"):
                err = status_info.get("error") or "Unknown agent failure"
                log.warning(f"Agent for task {t.id} failed: {err}")
                svc_update_task(
                    t.id, 
                    agent_state=AgentState.failed, 
                    status=TaskStatus.blocked,
                    blocked_reason=f"Agent execution failed: {err}"
                )
            elif run_status in ("stopped", "stop"):
                 log.info(f"Agent for task {t.id} was stopped.")
                 svc_update_task(
                    t.id, 
                    agent_state=AgentState.stopped,
                    # We don't necessarily change task status to blocked, 
                    # but if it was running, it usually means it didn't finish work.
                    # Let's verify if we should block it.
                    # For now, just mark agent_state.
                 )

    except Exception as e:
        log.exception(f"Error in reconciliation loop: {e}")


def run_loop(poll_interval: float, stop_event: threading.Event) -> None:
    log = logging.getLogger("orchestratord")
    log.info(f"Runner started. poll_interval={poll_interval}")

    while not stop_event.is_set():
        try:
            # 1. Reconcile states
            _reconcile_agent_states()

            # 2. Poll new tasks
            tasks = svc_list_tasks()
            # Iterate over a snapshot and pick one-by-one to reduce race windows
            for t in tasks:
                if stop_event.is_set():
                    break
                if _should_pick(t):
                    _process_task(t)
        except Exception as e:
            log.exception(f"Unexpected error in poll loop: {e}")
        # Sleep with small increments to be signal-responsive
        deadline = time.time() + poll_interval
        while time.time() < deadline and not stop_event.is_set():
            time.sleep(0.1)

    log.info("Runner stopped.")


def main(argv: Optional[list[str]] = None) -> int:
    _configure_logging()
    poll = _env_float("ORCH_POLL_INTERVAL", 5.0)

    stop = threading.Event()

    def _handle_sig(_signum, _frame):
        logging.getLogger("orchestratord").info("Signal received. Shutting down...")
        stop.set()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        run_loop(poll, stop)
        return 0
    except KeyboardInterrupt:
        stop.set()
        return 0
    except Exception as e:
        logging.getLogger("orchestratord").exception(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
