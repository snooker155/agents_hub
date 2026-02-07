from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass

from ..workspace import WORKSPACES_ROOT

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

@dataclass(frozen=True)
class RunSpec:
    cmd: List[str]
    env: Dict[str, str]
    cwd: str

def _resolve_workspace(task: Any, params: Optional[Dict[str, Any]]) -> Path:
    ws = (params or {}).get("workspace") or getattr(task, "workspace", None)
    if not ws:
        return Path.cwd()
    p = Path(str(ws)).expanduser()
    if not p.is_absolute():
        return (WORKSPACES_ROOT / p.name).resolve()
    return p.resolve()

def build_factory_run_spec(task: Any, agent_id: str, params: Optional[Dict[str, Any]] = None) -> RunSpec:
    workspace = _resolve_workspace(task, params)

    # Environment
    env = os.environ.copy()
    env["WORKSPACE_ROOT"] = str(workspace)

    # Special case for graph
    if agent_id == "factory-graph":
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run_graph.py"),
            "--desc", (params or {}).get("description") or getattr(task, "description", ""),
            "--workspace", str(workspace)
        ]
    elif agent_id == "factory-custom-graph" or agent_id == "custom-graph":
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run_custom_graph.py"),
            "--graph", (params or {}).get("description") or "{}",
            "--workspace", str(workspace),
            "--desc", getattr(task, "description", "")
        ]
    else:
        # Standard factory agents
        # Strip 'factory-' prefix if present
        actual_agent = agent_id[8:] if agent_id.startswith("factory-") else agent_id
        action = (params or {}).get("action")

        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run_agent.py"),
            actual_agent,
        ]
        if action:
            cmd.append(action)

        cmd.extend(["--workspace", str(workspace)])

        desc = (params or {}).get("description") or getattr(task, "description", "")
        if desc:
            cmd.extend(["--desc", desc])

        task_id = (params or {}).get("task_id")
        if task_id:
            cmd.extend(["--task-id", task_id])

    return RunSpec(cmd=cmd, env=env, cwd=str(workspace))
