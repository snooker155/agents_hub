from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, List
from dataclasses import dataclass

from common.workspace import WORKSPACES_ROOT, resolve_project_root, project_folder_name
from common.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class RunSpec:
    cmd: List[str]
    env: Dict[str, str]
    cwd: str

def _resolve_workspace(task: Any, params: Optional[Dict[str, Any]]) -> Path:
    ws = (params.get("workspace") if isinstance(params, dict) else None) or getattr(task, "workspace", None)
    if not ws:
        return Path.cwd()
    p = Path(str(ws)).expanduser()
    if not p.is_absolute():
        return (WORKSPACES_ROOT / p.name).resolve()
    return p.resolve()

def build_run_spec(task: Any, agent_id: str, params: Optional[Dict[str, Any]] = None) -> RunSpec:
    # Pydantic models are truthy, so `params or {}` wouldn't fall back to {};
    # normalise to a plain dict to keep all downstream .get() calls safe.
    if params is not None and not isinstance(params, dict):
        try:
            params = dict(params) if hasattr(params, "__iter__") else vars(params)
        except Exception:
            params = {}
    workspace = _resolve_workspace(task, params)

    # Resolve project subfolder — agents operate here; workspace root holds .workspace.json / .logs/
    project_name = getattr(task, "project", None) or (params or {}).get("project") or None
    # Fallback: derive folder name from the linked Project record when task.project is not set
    if not project_name:
        project_id = getattr(task, "project_id", None)
        if project_id:
            try:
                from pathlib import Path as _Path
                from projects.storage import ProjectStore as _PS
                _pstore = _PS(path=_Path(__file__).resolve().parents[1] / "projects" / "projects.json")
                _proj = _pstore.get(str(project_id))
                if _proj:
                    project_name = project_folder_name(_proj.name)
            except Exception:
                pass
    if project_name:
        project_path = resolve_project_root(workspace.name, str(project_name))
    else:
        project_path = workspace

    # Environment
    env = os.environ.copy()
    env["WORKSPACE_ROOT"] = str(workspace)   # workspace root (logs/settings live here)
    env["AGENT_WORKSPACE"] = workspace.name
    # Ensure the subprocess resolves tasks.json relative to the project root,
    # not the workspace cwd.  Without this, task status updates written by
    # run_agent.py land in a different file and are never seen by the server.
    tasks_file = str(settings.tasks_file or "tasks/tasks.json")
    if not Path(tasks_file).is_absolute():
        tasks_file = str((PROJECT_ROOT / tasks_file).resolve())
    env["TASKS_FILE"] = tasks_file
    
    # Ensure OpenAI API key is available in subprocess
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key

    # Derive AGENT_PROVIDER / AGENT_MODEL for the subprocess:
    # 1. Start from the system default (DEFAULT_PROVIDER + its model) set via the
    #    Settings UI and stored in .env.  We read .env directly here because
    #    pydantic_settings merges field defaults into Settings fields, making it
    #    impossible to distinguish "user configured" from "code default" via the
    #    settings object alone.
    # 2. Override with the workspace's explicit default model when it is not "global"
    #    (= "inherit system default").
    # Per-agent registry overrides applied below take the highest priority.
    try:
        from common.workspace import get_workspace_metadata as _get_ws_meta, get_workspace_default_model_config as _get_ws_default
        ws_meta = _get_ws_meta(workspace.name)
        if isinstance(ws_meta, dict):
            override = ws_meta.get("model_override") or {}
            ws_default = _get_ws_default(ws_meta)
        else:
            override = {}
            ws_default = {}

        op = (override.get("provider") or "").strip()
        if op and op not in ("global", "workspace_default"):
            # Explicit UI override — use it
            ws_provider, ws_model = op, (override.get("model") or "")
        elif op == "global":
            # User explicitly forced global provider — let .env take effect
            ws_provider, ws_model = "global", ""
        else:
            # No override → use workspace default model, or fall back to global
            dp = (ws_default.get("provider") or "").strip()
            ws_provider = dp if dp and dp != "global" else "global"
            ws_model = (ws_default.get("model") or "") if ws_provider != "global" else ""

        if ws_provider and ws_provider != "global":
            # Workspace has a concrete model — inject it so the subprocess
            # uses this instead of the service-level DEFAULT_PROVIDER from .env.
            env["AGENT_PROVIDER"] = ws_provider
            if ws_model:
                env["AGENT_MODEL"] = ws_model
        # else: workspace inherits the service default.  The subprocess creates its
        # own Settings instance that reads .env directly, so DEFAULT_PROVIDER and
        # its model are already available there — no injection needed.
    except Exception:
        pass

    # Strip 'factory-' prefix if present
    actual_agent = agent_id

    # Inject per-agent model overrides from registry into the subprocess environment
    try:
        from agents.registry import get_agent as _reg_get_agent
        spec = _reg_get_agent(actual_agent)
        if spec:
            if spec.provider and spec.provider != "inherit":
                env["AGENT_PROVIDER"] = spec.provider
            if spec.model:
                env["AGENT_MODEL"] = spec.model
            if spec.base_url:
                env["AGENT_BASE_URL"] = spec.base_url
            if spec.api_key:
                env["AGENT_API_KEY"] = spec.api_key
            if spec.temperature is not None:
                env["AGENT_TEMPERATURE"] = str(spec.temperature)
            if spec.max_tokens is not None:
                env["AGENT_MAX_TOKENS"] = str(spec.max_tokens)
    except Exception:
        pass  # If registry lookup fails, continue without overrides

    action = (params or {}).get("action")

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "run_agent.py"),
        actual_agent,
    ]
    if action:
        cmd.append(action)

    cmd.extend(["--workspace", str(project_path)])

    desc = (params or {}).get("description") or getattr(task, "description", "")
    if desc:
        cmd.extend(["--desc", desc])

    # Always pass task_id so the agent subprocess has full task context
    task_id = (params or {}).get("task_id") or (str(task.id) if task and hasattr(task, "id") else None)
    if task_id:
        cmd.extend(["--task-id", task_id])

    return RunSpec(cmd=cmd, env=env, cwd=str(project_path))
