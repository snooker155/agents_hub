#!/usr/bin/env python3
"""
Simple CLI for orchestrator tasks and agents.

Usage:
  # Tasks management
  python -m orchestrator.cli add "TITLE" --desc "..."
  python -m orchestrator.cli list
  python -m orchestrator.cli stop TASK_ID
  python -m orchestrator.cli block TASK_ID --reason "..."
  python -m orchestrator.cli sequence SEQ_NAME TASK_ID1 TASK_ID2 ...

  # Agents & task-agent controls
  python -m orchestrator.cli agents list
  python -m orchestrator.cli tasks list
  python -m orchestrator.cli tasks assign TASK_ID AGENT_ID [--params '{"k":"v"}']
  python -m orchestrator.cli tasks stop TASK_ID
  python -m orchestrator.cli tasks status TASK_ID

Notes:
- "add" creates a high-level task from the user and then attempts to run the
  agent to decompose it into subtasks. If agent dependencies or API key are not
  configured, the task is still created and a warning is printed.
- The CLI aims to have minimal external dependencies; it relies on the
  orchestrator package modules already present in this repository.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List
from uuid import UUID

from .tasks_service import (
    CreatedBy,
    Task,
    create_sequence as svc_create_sequence,
    create_task as svc_create_task,
    list_tasks as svc_list_tasks,
    stop_task as svc_stop_task,
    block_task as svc_block_task,
    get_task as svc_get_task,
    assign_agent as svc_assign_agent,
    set_agent_state as svc_set_agent_state,
    default_store,
)
from tasks import AgentState
from tasks.storage import TaskStore
from .workspace import (
    create_workspace_folder,
    get_workspace_folder,
    list_workspace_folders,
)
from .agents.registry import list_agents as reg_list_agents, get_agent as reg_get_agent
from .agents.run_manager import (
    start_run as rm_start_run,
    stop_run as rm_stop_run,
    get_status as rm_get_status,
)


def _parse_uuid(s: str) -> UUID:
    try:
        return UUID(s)
    except Exception:
        raise argparse.ArgumentTypeError(f"Invalid UUID: {s}")


def _get_store(args: argparse.Namespace) -> TaskStore:
    if getattr(args, "tasks_file", None):
        return TaskStore(args.tasks_file)
    return default_store


def cmd_list(args: argparse.Namespace) -> int:
    tasks: List[Task] = svc_list_tasks(store=_get_store(args))
    if not tasks:
        print("No tasks")
        return 0
    for t in tasks:
        print(f"{t.id} | {t.status.value} | {t.title}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    store = _get_store(args)
    
    # Create workspace if needed
    workspace_path_str = None
    if getattr(args, "workspace_name", None):
        # Use provided workspace name
        try:
            ws_path = create_workspace_folder(args.workspace_name)
            workspace_path_str = str(ws_path)
            print(f"Created/using workspace: {workspace_path_str}")
        except Exception as e:
            print(f"Failed to create workspace: {e}", file=sys.stderr)
            return 1
    else:
        # Create a new workspace with auto-generated name
        try:
            ws_path = create_workspace_folder()
            workspace_path_str = str(ws_path)
            print(f"Created workspace: {workspace_path_str}")
        except Exception as e:
            print(f"Failed to create workspace: {e}", file=sys.stderr)
            return 1
    
    # Store only the workspace folder name in task
    skip_decompose = bool(getattr(args, "no_decompose", False) or getattr(args, "no_agent", False))
    task = svc_create_task(
        title=args.title,
        description=args.desc or "",
        created_by=CreatedBy.user,
        workspace=Path(workspace_path_str).name if workspace_path_str else None,
        should_decompose=not skip_decompose,
        store=store,
    )
    print(f"Created task: {task.id} | {task.title}")

    # If not skipping, we can either wait for the runner or run it immediately
    if not skip_decompose and not getattr(args, "wait_for_runner", False):
        # Call decompose command handler for immediate action
        args.task_id = task.id
        return cmd_decompose(args)
    return 0


def cmd_decompose(args: argparse.Namespace) -> int:
    """Run agent to decompose a single task."""
    store = _get_store(args)
    task = svc_get_task(args.task_id, store=store)
    if not task:
        print(f"Task not found: {args.task_id}", file=sys.stderr)
        return 1

    try:
        from .agent import run_decomposing_agent
    except Exception as e:
        print(f"[warn] Agent not available: {e}", file=sys.stderr)
        return 1

    try:
        result = run_decomposing_agent(
            str(task.id),
            task.title,
            task.description,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            verbose=args.verbose,
        )
        out = result.get("output") if isinstance(result, dict) else None
        if out:
            print("Agent output:\n" + str(out))
        else:
            print("Agent executed.")
        return 0
    except RuntimeError as e:
        print(f"[warn] Agent skipped: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[warn] Agent failed: {e}", file=sys.stderr)
        return 1


def cmd_stop(args: argparse.Namespace) -> int:
    tid = args.task_id
    store = _get_store(args)
    updated = svc_stop_task(tid, store=store)
    if not updated:
        print(f"Task not found: {tid}", file=sys.stderr)
        return 1
    print(f"Stopped: {updated.id} | {updated.title}")
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    tid = args.task_id
    reason = args.reason
    store = _get_store(args)
    updated = svc_block_task(tid, reason, store=store)
    if not updated:
        print(f"Task not found: {tid}", file=sys.stderr)
        return 1
    print(f"Blocked: {updated.id} | reason: {updated.blocked_reason}")
    return 0


def cmd_sequence(args: argparse.Namespace) -> int:
    seq_name: str = args.name
    ids: List[UUID] = args.task_ids
    if not ids:
        print("No task IDs provided", file=sys.stderr)
        return 1
    store = _get_store(args)
    try:
        seq_id = svc_create_sequence(ids, sequence_id=seq_name, start_order=1, store=store)
        print(f"Sequence '{seq_id}' applied to {len(ids)} tasks in given order.")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# -------------------- Agents & Task-Agent management --------------------

def _print_task_with_agent_info(t: Task) -> None:
    agent = t.assigned_agent_type or "-"
    try:
        state = (t.agent_state.value if hasattr(t.agent_state, "value") else str(t.agent_state))
    except Exception:
        state = str(t.agent_state)
    run = rm_get_status(str(t.id))
    run_str = run.get("status") if isinstance(run, dict) else None
    suffix = f" | agent:{agent} | agent_state:{state}"
    if run_str:
        suffix += f" | run:{run_str}"
    print(f"{t.id} | {t.status.value} | {t.title}{suffix}")


def cmd_agents_list(_args: argparse.Namespace) -> int:
    try:
        specs = reg_list_agents()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not specs:
        print("No agents")
        return 0
    for s in specs:
        print(f"{s.id} | {s.type} | {s.name} | {s.entrypoint}")
    return 0


def cmd_tasks_list(args: argparse.Namespace) -> int:
    tasks: List[Task] = svc_list_tasks(store=_get_store(args))
    if not tasks:
        print("No tasks")
        return 0
    for t in tasks:
        _print_task_with_agent_info(t)
    return 0


def cmd_tasks_assign(args: argparse.Namespace) -> int:
    tid: UUID = args.task_id
    agent_id: str = args.agent_id
    params_json: str | None = args.params
    store = _get_store(args)

    # Validate task
    task = svc_get_task(tid, store=store)
    if not task:
        print(f"Task not found: {tid}", file=sys.stderr)
        return 1

    # Validate agent
    spec = reg_get_agent(agent_id)
    if not spec:
        print(f"Agent not found: {agent_id}", file=sys.stderr)
        return 1

    # Parse params
    import json as _json
    params = None
    if params_json:
        try:
            obj = _json.loads(params_json)
            if obj is not None and not isinstance(obj, dict):
                print("--params must be a JSON object", file=sys.stderr)
                return 1
            params = obj
        except Exception as e:
            print(f"Invalid --params JSON: {e}", file=sys.stderr)
            return 1

    # Enforce policy: decomposer-capable agent only for user-created tasks
    try:
        caps = list(getattr(spec, "capabilities", []) or [])
    except Exception:
        caps = []
    if "decompose" in caps and getattr(task, "created_by", None) != CreatedBy.user:
        print("Decomposer agent can only be assigned to user-created tasks", file=sys.stderr)
        return 1

    # Start agent run and record assignment/state
    try:
        run_id = rm_start_run(str(task.id), agent_id, params, foreground=args.foreground)
        svc_assign_agent(task.id, agent_id, params, run_id=run_id, store=store)
        svc_set_agent_state(task.id, AgentState.running, run_id=run_id, store=store)
        if not args.foreground:
            print(f"Assigned and started: task={task.id} | agent={agent_id} | run_id={run_id}")
        return 0
    except Exception as e:
        print(f"Failed to assign/start agent: {e}", file=sys.stderr)
        return 1


def cmd_tasks_stop(args: argparse.Namespace) -> int:
    tid: UUID = args.task_id
    store = _get_store(args)
    # Best-effort stop
    try:
        ok = rm_stop_run(str(tid))
        svc_set_agent_state(tid, AgentState.stopped, store=store)
        print(f"Agent stop signal sent: {ok}")
        return 0 if ok else 1
    except Exception as e:
        print(f"Failed to stop agent: {e}", file=sys.stderr)
        return 1


def cmd_workspace_create(args: argparse.Namespace) -> int:
    """Create a new workspace directory."""
    name = getattr(args, "workspace_name", None)
    try:
        path = create_workspace_folder(name)
        print(f"Created workspace: {path}")
        return 0
    except Exception as e:
        print(f"Failed to create workspace: {e}", file=sys.stderr)
        return 1


def cmd_workspace_list(_args: argparse.Namespace) -> int:
    """List all workspace directories."""
    try:
        folders = list_workspace_folders()
        if not folders:
            print("No workspaces found")
            return 0
        for folder in folders:
            print(f"  {folder.name} -> {folder}")
        return 0
    except Exception as e:
        print(f"Failed to list workspaces: {e}", file=sys.stderr)
        return 1


def cmd_tasks_status(args: argparse.Namespace) -> int:
    tid: UUID = args.task_id
    store = _get_store(args)
    try:
        task = svc_get_task(tid, store=store)
        status = rm_get_status(str(tid))
        if not task and not status:
            print(f"Task not found: {tid}", file=sys.stderr)
            return 1
        # Print a compact, human-readable status
        agent = task.assigned_agent_type if task else None
        try:
            state = (task.agent_state.value if task and hasattr(task.agent_state, "value") else (str(task.agent_state) if task else None))
        except Exception:
            state = str(task.agent_state) if task else None
        print(f"Task {tid}")
        print(f"  agent: {agent or '-'}")
        print(f"  agent_state: {state or '-'}")
        print(
            f"  run_id: {(task.assigned_agent_run_id if task else None) or (status.get('run_id') if isinstance(status, dict) else '-') }"
        )
        if status:
            pid = status.get("pid") if isinstance(status, dict) else None
            st = status.get("status") if isinstance(status, dict) else None
            exit_code = status.get("exit_code") if isinstance(status, dict) else None
            print(f"  run: status={st} pid={pid} exit_code={exit_code}")
        else:
            print("  run: -")
        return 0
    except Exception as e:
        print(f"Failed to get status: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orchestrator.cli", description="Task orchestrator CLI")
    p.add_argument("--tasks-file", help="Path to the tasks file (JSON or YAML)")
    sub = p.add_subparsers(dest="command", required=True)

    # list
    sp_list = sub.add_parser("list", help="List tasks (id, status, title)")
    sp_list.set_defaults(func=cmd_list)

    # add
    sp_add = sub.add_parser("add", help="Add a high-level task")
    sp_add.add_argument("title", help="Task title")
    sp_add.add_argument("--desc", default="", help="Task description")
    sp_add.add_argument(
        "--workspace-name",
        default=None,
        help="Workspace name for this task (creates under workspaces/). If not provided, auto-generates a name.",
    )
    # Backwards-compatible flags to skip decomposition
    sp_add.add_argument(
        "--no-decompose",
        action="store_true",
        help="Do not run the agent to decompose the task after creating it",
    )
    sp_add.add_argument(
        "--no-agent",
        action="store_true",
        help="Alias for --no-decompose (kept for compatibility)",
    )
    # Agent flags for optional decomposition
    sp_add.add_argument("--model", default=None, help="LLM model override for decomposition")
    sp_add.add_argument("--temperature", type=float, default=None, help="LLM temperature override for decomposition")
    sp_add.add_argument("--max-tokens", type=int, default=None, help="LLM max tokens override for decomposition")
    sp_add.add_argument("-v", "--verbose", action="store_true", help="Verbose agent execution for decomposition")
    sp_add.set_defaults(func=cmd_add)

    # decompose
    sp_decompose = sub.add_parser("decompose", help="Run decomposition agent for a task")
    sp_decompose.add_argument("task_id", type=_parse_uuid, help="Task UUID")
    sp_decompose.add_argument("--model", default=None, help="LLM model override")
    sp_decompose.add_argument("--temperature", type=float, default=None, help="LLM temperature override")
    sp_decompose.add_argument("--max-tokens", type=int, default=None, help="LLM max tokens override")
    sp_decompose.add_argument("-v", "--verbose", action="store_true", help="Verbose agent execution")
    sp_decompose.set_defaults(func=cmd_decompose)

    # stop
    sp_stop = sub.add_parser("stop", help="Stop a task by ID")
    sp_stop.add_argument("task_id", type=_parse_uuid, help="Task UUID")
    sp_stop.set_defaults(func=cmd_stop)

    # block
    sp_block = sub.add_parser("block", help="Block a task by ID with a reason")
    sp_block.add_argument("task_id", type=_parse_uuid, help="Task UUID")
    sp_block.add_argument("--reason", required=True, help="Reason for blocking")
    sp_block.set_defaults(func=cmd_block)

    # sequence
    sp_seq = sub.add_parser("sequence", help="Create/apply a sequence to tasks in given order")
    sp_seq.add_argument("name", help="Sequence name/id (string)")
    sp_seq.add_argument("task_ids", nargs=argparse.REMAINDER, type=_parse_uuid, help="Task UUIDs in order")
    sp_seq.set_defaults(func=cmd_sequence)

    # agents namespace
    sp_agents = sub.add_parser("agents", help="Agents registry commands")
    sub_agents = sp_agents.add_subparsers(dest="agents_cmd", required=True)
    sp_agents_list = sub_agents.add_parser("list", help="List available agents")
    sp_agents_list.set_defaults(func=cmd_agents_list)

    # tasks namespace
    sp_tasks = sub.add_parser("tasks", help="Task-related utilities (with agent info)")
    sub_tasks = sp_tasks.add_subparsers(dest="tasks_cmd", required=True)
    # tasks:list with enhanced output
    sp_tasks_list = sub_tasks.add_parser("list", help="List tasks with agent assignment and run state")
    sp_tasks_list.set_defaults(func=cmd_tasks_list)
    # tasks:assign
    sp_tasks_assign = sub_tasks.add_parser("assign", help="Assign and start an agent for a task")
    sp_tasks_assign.add_argument("task_id", type=_parse_uuid, help="Task UUID")
    sp_tasks_assign.add_argument("agent_id", help="Agent id from registry")
    sp_tasks_assign.add_argument("--params", default=None, help="Optional JSON object with agent params")
    sp_tasks_assign.add_argument(
        "--foreground",
        action="store_true",
        help="Run the agent in the foreground and stream its output",
    )
    sp_tasks_assign.set_defaults(func=cmd_tasks_assign)
    # tasks:stop
    sp_tasks_stop = sub_tasks.add_parser("stop", help="Stop the latest running agent for a task")
    sp_tasks_stop.add_argument("task_id", type=_parse_uuid, help="Task UUID")
    sp_tasks_stop.set_defaults(func=cmd_tasks_stop)
    # tasks:status
    sp_tasks_status = sub_tasks.add_parser("status", help="Show agent/run status for a task")
    sp_tasks_status.add_argument("task_id", type=_parse_uuid, help="Task UUID")
    sp_tasks_status.set_defaults(func=cmd_tasks_status)

    # workspace namespace
    sp_ws = sub.add_parser("workspace", help="Workspace management commands")
    sub_ws = sp_ws.add_subparsers(dest="workspace_cmd", required=True)
    # workspace:create
    sp_ws_create = sub_ws.add_parser("create", help="Create a new workspace directory")
    sp_ws_create.add_argument("workspace_name", nargs="?", default=None, help="Optional workspace name (auto-generated if not provided)")
    sp_ws_create.set_defaults(func=cmd_workspace_create)
    # workspace:list
    sp_ws_list = sub_ws.add_parser("list", help="List all workspaces")
    sp_ws_list.set_defaults(func=cmd_workspace_list)

    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(bool(args.func(args)))  # func returns 0 on success, 1 on error


if __name__ == "__main__":
    sys.exit(main())
