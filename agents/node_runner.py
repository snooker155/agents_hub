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

# Ensure project root is on sys.path when running as a module
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Logging ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{_now()}] {msg}", flush=True)


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
    log(f"Orchestrator node {node_id[:8]} ready — polling every 10s")
    _set_status(node_id, "running")

    POLL = 10  # seconds between sweeps

    while True:
        try:
            from common import tasks_service
            from tasks import TaskStatus, AgentState, CreatedBy
            from agents.factory import create_agent

            tasks = tasks_service.list_tasks()
            pending = [
                t for t in tasks
                if t.status == TaskStatus.todo
                and t.agent_state == AgentState.none
                and t.created_by == CreatedBy.user
            ]

            for task in pending:
                # Resolve workspace
                abs_ws = workspace
                if task.workspace:
                    try:
                        from common.workspace import create_workspace_folder
                        abs_ws = str(create_workspace_folder(task.workspace))
                    except Exception:
                        pass

                log(f"Picking up task {str(task.id)[:8]}: {task.title!r}")
                try:
                    tasks_service.set_agent_state(task.id, AgentState.running)
                    agent = create_agent("orchestrator", workspace=abs_ws)
                    prompt = (
                        f"Task ID: {task.id}\n"
                        f"Title: {task.title}\n"
                        f"Description: {task.description or 'No description'}\n\n"
                        "Analyse this task and coordinate its execution by assigning "
                        "appropriate specialized agents."
                    )
                    result = agent.run(prompt)
                    if result.ok:
                        log(f"Task {str(task.id)[:8]} orchestrated successfully")
                        tasks_service.set_agent_state(task.id, AgentState.completed)
                    else:
                        log(f"Task {str(task.id)[:8]} orchestration error: {result.error}")
                        tasks_service.set_agent_state(task.id, AgentState.failed)
                        tasks_service.block_task(task.id, reason=result.error or "orchestrator error")
                except Exception as exc:
                    log(f"Error on task {str(task.id)[:8]}: {exc}")
                    try:
                        tasks_service.set_agent_state(task.id, AgentState.failed)
                    except Exception:
                        pass

        except Exception as exc:
            log(f"Loop error: {exc}\n{traceback.format_exc()}")

        time.sleep(POLL)


# ── Worker loop ───────────────────────────────────────────────────────────────

def run_worker_loop(node_id: str, agent_id: str, workspace: str | None) -> None:
    """Poll for tasks whose assigned_agent_type == agent_id and run them."""
    log(f"Worker node {node_id[:8]} ({agent_id}) ready — polling every 10s")
    _set_status(node_id, "running")

    POLL = 10

    while True:
        try:
            from common import tasks_service
            from tasks import TaskStatus, AgentState
            from agents.factory import create_agent

            tasks = tasks_service.list_tasks()
            assigned = [
                t for t in tasks
                if t.assigned_agent_type == agent_id
                and t.agent_state == AgentState.assigned
            ]

            for task in assigned:
                abs_ws = workspace
                if task.workspace:
                    try:
                        from common.workspace import create_workspace_folder
                        abs_ws = str(create_workspace_folder(task.workspace))
                    except Exception:
                        pass

                log(f"Processing task {str(task.id)[:8]}: {task.title!r}")
                try:
                    tasks_service.set_agent_state(task.id, AgentState.running)
                    agent = create_agent(agent_id, workspace=abs_ws)
                    prompt = f"{task.title}\n\n{task.description or ''}"
                    result = agent.run(prompt)

                    if result.ok:
                        log(f"Task {str(task.id)[:8]} done")
                        tasks_service.set_agent_state(task.id, AgentState.completed)
                        tasks_service.update_task(task.id, status=TaskStatus.done)
                    else:
                        log(f"Task {str(task.id)[:8]} failed: {result.error}")
                        tasks_service.set_agent_state(task.id, AgentState.failed)
                        tasks_service.block_task(task.id, reason=result.error or "worker error")
                except Exception as exc:
                    log(f"Error on task {str(task.id)[:8]}: {exc}")
                    try:
                        tasks_service.set_agent_state(task.id, AgentState.failed)
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
    parser.add_argument("--log-file", default=None)  # stdout already redirected by node_manager
    args = parser.parse_args()

    log(f"=== Node {args.node_id[:8]} starting  agent={args.agent_id} ===")

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
