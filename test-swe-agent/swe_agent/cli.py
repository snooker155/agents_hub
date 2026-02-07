#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import update_config as _update_cfg, get_config as _get_cfg
from .tools.patch import apply_unified_diff as _apply_unified_diff, create_file as _create_file
from .tools.fs import write_file as _write_file, read_file as _read_file, list_files as _list_files


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swe-agent", description="SWE Agent CLI")
    p.add_argument(
        "--tasks-file",
        help="Path to the tasks file (JSON or YAML).",
        default=None,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # Agent-powered commands (LLM). Imports are done lazily in main() to avoid import errors in tests.
    sp_run = sub.add_parser("run", help="Run SWE agent for a task id from tasks file (requires LLM)")
    sp_run.add_argument("task_id", help="Task id (UUID or string)")
    sp_run.add_argument("--workspace", help="Override task workspace for this run", default=None)
    sp_run.add_argument("--model", default=None)
    sp_run.add_argument("--temperature", type=float, default=None)
    sp_run.add_argument("--max-tokens", type=int, default=None)
    sp_run.add_argument("-v", "--verbose", action="store_true")
    sp_run.set_defaults(cmd="run")

    sp_text = sub.add_parser("text", help="Run SWE agent on an ad-hoc text goal (requires LLM)")
    sp_text.add_argument("text", help="Free-form goal/instructions for the agent")
    sp_text.add_argument("--model", default=None)
    sp_text.add_argument("--temperature", type=float, default=None)
    sp_text.add_argument("--max-tokens", type=int, default=None)
    sp_text.add_argument("-v", "--verbose", action="store_true")
    sp_text.set_defaults(cmd="text")

    # Internal orchestrator entrypoint (hidden)
    sp_internal_run = sub.add_parser("_run", help=argparse.SUPPRESS)
    sp_internal_run.add_argument("--instruction", required=True)
    sp_internal_run.add_argument("--workspace", required=True)
    sp_internal_run.add_argument("--max-tool-calls", type=int, default=30)
    sp_internal_run.add_argument("--system-prompt", default=None)
    sp_internal_run.add_argument("--config", default=None, help="JSON string with sandbox limits")
    sp_internal_run.add_argument("-v", "--verbose", action="store_true")
    sp_internal_run.set_defaults(cmd="_run")

    # Filesystem utility subcommands (no LLM)
    sp_fs = sub.add_parser("fs", help="Filesystem operations (no LLM)")
    sp_fs.add_argument(
        "--workspace",
        help="Workspace root for filesystem operations.",
        default=None,
    )
    fs_sub = sp_fs.add_subparsers(dest="fs_cmd", required=True)

    sp_apply = fs_sub.add_parser("apply-diff", help="Apply a unified diff to files under --workspace")
    g = sp_apply.add_mutually_exclusive_group(required=True)
    g.add_argument("--diff-text", help="Unified diff text provided inline", default=None)
    g.add_argument("--diff-file", help="Path to a file containing unified diff", default=None)

    sp_create = fs_sub.add_parser("create-file", help="Create a new file under --workspace")
    sp_create.add_argument("path", help="Path to create (relative to workspace)")
    sp_create.add_argument("content", help="Initial content for the file")

    sp_write = fs_sub.add_parser("write-file", help="Write text to a file under --workspace")
    sp_write.add_argument("path", help="Path to write (relative to workspace)")
    sp_write.add_argument("content", help="Content to write")

    sp_read = fs_sub.add_parser("read-file", help="Read a text file under --workspace")
    sp_read.add_argument("path", help="Path to read (relative to workspace)")

    sp_list = fs_sub.add_parser("list-files", help="List files under --workspace by glob")
    sp_list.add_argument("--glob", default="**/*", help="Glob pattern relative to workspace")

    return p


def _ensure_workspace(ws: Optional[str]) -> Optional[str]:
    if ws is not None:
        _update_cfg(workspace_root=ws)
        return ws
    # If not provided, use configured root if any; otherwise current CWD in tools will be used
    return _get_cfg().workspace_root.as_posix() if _get_cfg().workspace_root else None


def _find_tasks_file(provided_path: Optional[str]) -> Optional[str]:
    """Find tasks file: use provided path, or check tasks/tasks.json, or default."""
    if provided_path:
        return provided_path
    
    # Try to find tasks/tasks.json relative to current working directory
    tasks_path = Path("tasks/tasks.json")
    if tasks_path.exists():
        return str(tasks_path.resolve())
    
    # Also try parent directory
    parent_tasks = Path("../tasks/tasks.json")
    if parent_tasks.exists():
        return str(parent_tasks.resolve())
    
    return None  # Return None to use TaskStore default


def _cmd_fs(args: argparse.Namespace) -> int:
    """Handler for all 'fs' subcommands."""
    eff_ws = _ensure_workspace(args.workspace)
    try:
        if args.fs_cmd == "apply-diff":
            if not eff_ws:
                print("Error: --workspace is required for fs apply-diff", file=sys.stderr)
                return 1
            diff_text = args.diff_text
            if args.diff_file:
                try:
                    with open(args.diff_file, "r", encoding="utf-8") as f:
                        diff_text = f.read()
                except Exception as e:
                    print(f"Error: failed to read diff file: {e}", file=sys.stderr)
                    return 1
            result = _apply_unified_diff(diff_text, workspace=eff_ws)
            print(json.dumps(result, ensure_ascii=False))
        elif args.fs_cmd == "create-file":
            if not eff_ws:
                print("Error: --workspace is required for fs create-file", file=sys.stderr)
                return 1
            rel = _create_file(args.path, args.content, workspace=eff_ws)
            print(json.dumps({"path": rel}, ensure_ascii=False))
        elif args.fs_cmd == "write-file":
            rel = _write_file(args.path, args.content, create_dirs=True)
            print(json.dumps({"path": rel}, ensure_ascii=False))
        elif args.fs_cmd == "read-file":
            content = _read_file(args.path)
            print(json.dumps({"path": args.path, "content": content}, ensure_ascii=False))
        elif args.fs_cmd == "list-files":
            files = _list_files(args.glob)
            print(json.dumps({"files": files}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main(argv: List[str] | None = None) -> int:
    # Popuplate env (e.g. OPENAI_API_KEY) from .env file if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "fs":
        return _cmd_fs(args)

    # Lazy import for agent commands to avoid heavy deps in fs-only usage
    if args.command == "run":
        from .api import run_task
        tasks_file = _find_tasks_file(args.tasks_file)
        print(f"Using tasks file: {tasks_file}")
        res = run_task(
            args.task_id,
            tasks_file=tasks_file,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            verbose=args.verbose,
            workspace_override=args.workspace,
        )
        if res.ok:
            if res.agent_output:
                print(res.agent_output)
            return 0
        else:
            print(f"Error: {res.error}", file=sys.stderr)
            return 1
    elif args.command == "text":
        from .api import run_text
        res = run_text(
            args.text,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            verbose=args.verbose,
        )
        if res.ok:
            if res.agent_output:
                print(res.agent_output)
            return 0
        else:
            print(f"Error: {res.error}", file=sys.stderr)
            return 1
    elif args.command == "_run":
        from .agent import run_task as run_task_low_level
        from pathlib import Path

        cfg = {}
        if args.config:
            try:
                cfg = json.loads(args.config)
                if not isinstance(cfg, dict):
                    raise ValueError("Config must be a JSON object")
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Error: invalid --config: {e}", file=sys.stderr)
                return 1

        # In low-level mode, config must be updated before tool init
        if cfg:
            _update_cfg(**cfg)

        res = run_task_low_level(
            instruction=args.instruction,
            workspace=Path(args.workspace),
            max_tool_calls=args.max_tool_calls,
            system_prompt=args.system_prompt,
            verbose=bool(args.verbose),
        )
        print(res.model_dump_json())
        return 0 if res.ok else 1
    else:
        # Should not happen if parser is correct
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
