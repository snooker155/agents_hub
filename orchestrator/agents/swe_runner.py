from __future__ import annotations

"""
SWE Agent runner adapter for the orchestrator.

This module prepares a subprocess run-spec to execute swe_agent using either
its public Python API (preferred) or the CLI as a fallback. The run-spec can be
consumed by AgentRunManager (or any other process supervisor) to spawn the
agent in a controlled environment.

Responsibilities:
- Build high-level agent instruction from an orchestrator task object.
- Resolve workspace path and optional sandbox limits (max_read_bytes, ignore_globs,
  allow_delete, binary_threshold) and max_tool_calls.
- Choose the best execution method:
     1) API mode: invoke swe_agent.agent.run_task via a tiny Python -c
         shim so we can pass parameters reliably (not used in current CLI-based flow).
    2) CLI mode: fall back to `python -m swe_agent.cli text ...` and rely on
       CWD as the workspace root.
- Return a RunSpec: {cmd: list[str], env: dict[str,str], cwd: str}

Notes on CLI fallback:
- swe_agent CLI does not currently expose a workspace-aware LLM run entrypoint,
  but its tools default to using the current working directory. Therefore, in
  CLI mode we set cwd=workspace so that filesystem tools are restricted to that
  directory implicitly.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
import json
import os
import sys
from ..workspace import WORKSPACES_ROOT

try:
    # Optional: Orchestrator settings for OPENAI_API_KEY passthrough
    from ..config import get_settings as _get_settings  # type: ignore
except Exception:  # pragma: no cover - settings not required at import time
    _get_settings = None  # type: ignore


@dataclass(frozen=True)
class RunSpec:
    cmd: List[str]
    env: Dict[str, str]
    cwd: str


# -------------------- Heuristics helpers --------------------

def _get(task: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        try:
            if hasattr(task, name):
                v = getattr(task, name)
                if v is not None:
                    return v
        except Exception:
            pass
        try:
            if isinstance(task, Mapping) and name in task:  # type: ignore[arg-type]
                v = task[name]  # type: ignore[index]
                if v is not None:
                    return v
        except Exception:
            pass
    return default


def _get_nested(task: Any, containers: Iterable[str], names: Iterable[str]) -> Any:
    for container in containers:
        obj = _get(task, container)
        if obj is None:
            continue
        for n in names:
            v = _get(obj, n)
            if v is not None:
                return v
    return None


def _resolve_workspace(task: Any, params: Optional[Dict[str, Any]]) -> Path:
    """Resolve the workspace directory for a SWE agent run.

    Precedence:
    1) Explicit params (workspace/workspace_root/repo_path/path)
    2) Task.workspace if present
    3) Other task hints (workspace/repo_path/repository_path/project_root/repo/path)
    4) Current working directory
    """
    ws = None
    # 1) Params override
    if params:
        ws = _get(params, "workspace", "workspace_root", "repo_path", "path")
    # 2) Task's bound workspace
    if ws is None:
        ws = _get(task, "workspace")
    # 3) Other task hints
    if ws is None:
        ws = _get(
            task,
            "workspace",
            "repo_path",
            "repository_path",
            "project_root",
            "repo",
            "path",
        )
    # 4) Fallback
    if ws is None:
        ws = Path.cwd()
    p = Path(str(ws)).expanduser()
    # If workspace looks like a simple name, resolve under WORKSPACES_ROOT
    if not p.is_absolute():
        return (WORKSPACES_ROOT / p.name).resolve()
    return p.resolve()


def _resolve_max_tool_calls(task: Any, params: Optional[Dict[str, Any]], default: int = 30) -> int:
    v = None
    if params:
        v = _get(params, "max_tool_calls", "max_calls")
        if v is None:
            v = _get_nested(params, containers=("limits", "constraints"), names=("max_tool_calls", "max_calls"))
    if v is None:
        v = _get(task, "max_tool_calls")
    if v is None:
        v = _get_nested(task, containers=("limits", "constraints"), names=("max_tool_calls", "max_calls"))
    try:
        return int(v) if v is not None else int(default)
    except Exception:
        return int(default)


def _resolve_limits(task: Any, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    limits: Dict[str, Any] = {}

    def pick(name: str, *aliases: str) -> Any:
        keys = (name,) + aliases
        # params first
        val = _get(params or {}, *keys)
        if val is None:
            val = _get_nested(params or {}, containers=("limits", "constraints"), names=keys)
        if val is None:
            val = _get(task, *keys)
        if val is None:
            val = _get_nested(task, containers=("limits", "constraints"), names=keys)
        return val

    max_read = pick("max_read_bytes")
    if max_read is not None:
        try:
            limits["max_read_bytes"] = int(max_read)
        except Exception:
            pass

    ignore = pick("ignore_globs")
    if isinstance(ignore, (list, tuple)):
        limits["ignore_globs"] = list(ignore)

    allow_delete = pick("allow_delete")
    if allow_delete is not None:
        limits["allow_delete"] = bool(allow_delete)

    binary_threshold = pick("binary_threshold")
    if binary_threshold is not None:
        try:
            limits["binary_threshold"] = int(binary_threshold)
        except Exception:
            pass

    return limits


def _build_instruction(task: Any, params: Optional[Dict[str, Any]]) -> str:
    tid = _get(task, "id") or (params or {}).get("task_id")
    title = _get(task, "title") or ""
    desc = _get(task, "description") or ""
    return (
        "Реши задачу целиком, используя доступные инструменты для работы с кодом.\n\n"
        f"ID: {tid}\n"
        f"Название: {title}\n"
        f"Описание:\n{desc}\n\n"
        "Действуй пошагово: читай файлы, меняй код через apply_unified_diff или write_file.\n"
    )


# -------------------- Public API --------------------

def build_run_spec(task: Any, params: Optional[Dict[str, Any]] = None) -> RunSpec:
    """Build a process run spec for executing swe_agent on the given task.

    Returns:
        RunSpec(cmd, env, cwd) suitable for subprocess.Popen.
    """
    workspace = _resolve_workspace(task, params)
    max_tool_calls = _resolve_max_tool_calls(task, params, default=30)
    limits = _resolve_limits(task, params)
    instruction = _build_instruction(task, params)
    system_prompt = _get(params or {}, "system_prompt") or None

    # Environment: inherit and pass through OPENAI_API_KEY if configured via orchestrator
    env = os.environ.copy()
    if _get_settings is not None:
        try:
            st = _get_settings()
            if st.openai_api_key and not env.get("OPENAI_API_KEY"):
                env["OPENAI_API_KEY"] = st.openai_api_key
        except Exception:
            pass

    # Use the new low-level CLI entrypoint
    cmd = [
        sys.executable,
        "-m",
        "swe_agent.cli",
        "_run",
        "--instruction",
        instruction,
        "--workspace",
        str(workspace),
        "--max-tool-calls",
        str(max_tool_calls),
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if limits:
        cmd.extend(["--config", json.dumps(limits, ensure_ascii=False)])

    return RunSpec(cmd=cmd, env=env, cwd=str(workspace))


__all__ = ["RunSpec", "build_run_spec"]
