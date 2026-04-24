from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, TypeVar

from common.workspace import get_workspace_metadata

T = TypeVar("T")


def normalize_workspace_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return Path(raw).name


def resolve_active_workspace(preferred: Optional[str] = None) -> Optional[str]:
    for candidate in (
        preferred,
        os.getenv("AGENT_WORKSPACE"),
        os.getenv("WORKSPACE_NAME"),
    ):
        ws = normalize_workspace_name(candidate)
        if ws:
            return ws
    ws_root = os.getenv("WORKSPACE_ROOT")
    if ws_root:
        return normalize_workspace_name(ws_root)
    # Fall back to the workspace stored by the UI
    try:
        from common.user_context import get_active_workspace
        stored = get_active_workspace()
        if stored:
            return normalize_workspace_name(stored)
    except Exception:
        pass
    return None


def task_in_workspace(task: object, workspace: Optional[str]) -> bool:
    ws = normalize_workspace_name(workspace)
    if not ws:
        return True
    task_ws = normalize_workspace_name(getattr(task, "workspace", None))
    return task_ws == ws


def filter_tasks_for_workspace(tasks: Iterable[T], workspace: Optional[str]) -> List[T]:
    ws = normalize_workspace_name(workspace)
    if not ws:
        return list(tasks)
    return [t for t in tasks if task_in_workspace(t, ws)]


def filter_agents_for_workspace(specs: Iterable[T], workspace: Optional[str]) -> List[T]:
    ws = normalize_workspace_name(workspace)
    if not ws:
        return list(specs)

    meta = get_workspace_metadata(ws) or {}
    allowed = meta.get("allowed_agents")
    privileged = {"orchestrator", "decomposer"}
    out: List[T] = []

    for spec in specs:
        if isinstance(spec, dict):
            agent_id = str(spec.get("id") or "")
            default_only = bool(spec.get("default_workspace_only", False))
        else:
            agent_id = str(getattr(spec, "id", "") or "")
            default_only = bool(getattr(spec, "default_workspace_only", False))

        if allowed is not None and agent_id not in allowed and agent_id not in privileged:
            continue
        if ws != "default" and default_only:
            continue
        out.append(spec)
    return out

