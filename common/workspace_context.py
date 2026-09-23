from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Iterable, List, Optional, TypeVar

from workspace import get_workspace_metadata

log = logging.getLogger(__name__)

T = TypeVar("T")

# Thread/async-safe workspace context — set by the chat route and node runner
# before invoking an agent so tool calls see the correct workspace without
# relying on a process-wide env var (which races under concurrent requests).
_workspace_ctx: ContextVar[Optional[str]] = ContextVar("active_workspace", default=None)

# Companion to ``_workspace_ctx``: the active project id for the current run.
# Set by the chat route when a project is selected, so project-scoped tools (e.g.
# list_tasks) can narrow within the workspace without a process-wide global.
_project_ctx: ContextVar[Optional[str]] = ContextVar("active_project", default=None)


def normalize_workspace_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return Path(raw).name


def workspace_name_from_path(value: Optional[str]) -> Optional[str]:
    """Extract the bare workspace name from an agent's operating path.

    Unlike ``normalize_workspace_name`` (which just takes the basename), this
    handles a project operating path: agents for tasks run in
    ``WORKSPACES_ROOT/<ws>/<project>``, whose basename is ``<project>``, not the
    workspace. Taking the first path component *under* WORKSPACES_ROOT recovers
    ``<ws>`` for both a bare workspace root and a project subfolder, so
    workspace-scoped lookups (model override, settings) resolve correctly. Falls
    back to the basename for inputs that are not absolute paths under the root.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        try:
            from workspace import WORKSPACES_ROOT
            rel = p.resolve().relative_to(Path(WORKSPACES_ROOT).resolve())
            if rel.parts:
                return rel.parts[0]
        except ValueError:
            pass
    return p.name


def resolve_active_workspace(preferred: Optional[str] = None) -> Optional[str]:
    for candidate in (
        preferred,
        _workspace_ctx.get(),
        os.getenv("AGENT_WORKSPACE"),
    ):
        ws = normalize_workspace_name(candidate)
        if ws:
            return ws
    # Fall back to the workspace stored by the UI
    try:
        from common.user_context import get_active_workspace
        stored = get_active_workspace()
        if stored:
            return normalize_workspace_name(stored)
    except Exception:  # noqa: BLE001 - the UI-stored fallback must not break workspace resolution
        log.debug("could not read the UI-stored active workspace", exc_info=True)
    return None


def normalize_project_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    return raw or None


def resolve_active_project(preferred: Optional[str] = None) -> Optional[str]:
    """The project id to scope the current run to, or None.

    Mirrors ``resolve_active_workspace`` but for projects: an explicit argument
    wins, then the context var set by the chat route. There is no env-var or
    UI-stored fallback — project scope is per-request only.
    """
    return normalize_project_id(preferred) or normalize_project_id(_project_ctx.get())


def filter_tasks_for_project(tasks: Iterable[T], project_id: Optional[str]) -> List[T]:
    pid = normalize_project_id(project_id)
    if not pid:
        return list(tasks)
    return [t for t in tasks if normalize_project_id(getattr(t, "project_id", None)) == pid]


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
    # System agents stay visible even if a workspace's allowed_agents somehow
    # lost them: every workspace must be able to reach all of them. Computed
    # from the registry (see workspace.system_agent_ids), which replaced a
    # hardcoded {orchestrator, decomposer} pair here.
    from workspace.storage import system_agent_ids
    privileged = set(system_agent_ids())
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
