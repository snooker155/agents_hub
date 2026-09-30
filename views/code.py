"""
Code view support: the pure logic behind the `code` view kind
(views/models.py CodeSpec) that the routes and the store share, so
dashboard/backend/routes/views.py stays a thin HTTP layer.

Persistence (versions, run history, save-to-project history) lives in
views/store.py, next to the rest of the view store, in JSON files kept
alongside checkpoints.json/clips/. This module holds:

- which CodeSpec ``language`` names are actually runnable, and what
  ``tools.run_code`` language they map to;
- a unified diff between two recorded versions;
- where a snippet lands when it is saved into a project, reusing the same
  project/workspace resolution the projects routes use.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Optional

# A CodeSpec ``language`` -> the ``tools.run_code`` language it runs as.
# Includes the short aliases the frontend's own runnable check accepts
# (dashboard/frontend/src/lib/highlight.js RUNNABLE_LANGUAGES), so a spec never
# shows an enabled Run button the backend then refuses. Anything else (sql,
# go, rust, …) is display-only: valid on a CodeSpec, but not runnable.
RUNNABLE_LANGUAGE_MAP = {
    "python": "python",
    "py": "python",
    "node": "node",
    "javascript": "node",
    "js": "node",
    "bash": "bash",
    "sh": "bash",
    "shell": "bash",
}


def runner_language(language: str) -> Optional[str]:
    """The ``tools.run_code`` language a CodeSpec ``language`` maps to, or
    ``None`` when it is not runnable (display-only)."""
    return RUNNABLE_LANGUAGE_MAP.get((language or "").strip().lower())


def is_runnable(language: str) -> bool:
    return runner_language(language) is not None


def diff_version_list(versions_list: list, a: int, b: int, what: str = "snippet") -> str:
    """A unified diff between two entries of a recorded version list (the shape
    views.store keeps for a code view and for a keyed snippet). Raises
    :class:`ValueError` when either version is not recorded."""
    versions = {int(v["version"]): v for v in versions_list}
    if int(a) not in versions:
        raise ValueError(f"no version {a} recorded for {what}")
    if int(b) not in versions:
        raise ValueError(f"no version {b} recorded for {what}")
    va, vb = versions[int(a)], versions[int(b)]
    lines = difflib.unified_diff(
        (va.get("body") or "").splitlines(keepends=True),
        (vb.get("body") or "").splitlines(keepends=True),
        fromfile=f"v{a}", tofile=f"v{b}",
    )
    return "".join(lines)


def diff_versions(view_id: str, a: int, b: int) -> str:
    """A unified diff between two recorded versions of a code view's body.

    Raises :class:`ValueError` (safe to surface as a 404) when the view has no
    such version recorded — an unknown view id reads the same way, as "no
    version recorded", rather than a separate case.
    """
    from views.store import list_code_versions

    return diff_version_list(list_code_versions(view_id), a, b, what=f"view {view_id}")


def resolve_project_save_path(project_id: str, rel_path: str) -> Path:
    """Resolve ``rel_path`` inside project ``project_id``'s own folder.

    Reuses the same project/workspace resolution
    dashboard/backend/routes/projects.py's ``_project_root_path`` uses
    (projects.storage.ProjectStore + workspace.get_workspace_folder /
    project_folder_name) rather than a copy of it, and creates the project
    folder if it does not exist yet (mirroring
    workspace.resolve_project_root, which the project-create route already
    calls once up front). Raises :class:`ValueError` with a message safe to
    hand back to an HTTP caller as a 400/404: unknown project, no workspace
    folder, an absolute path, or a path that would land outside the project's
    own folder (``..`` segments).
    """
    from projects.storage import ProjectStore
    from workspace import get_workspace_folder, project_folder_name

    project = ProjectStore().get(project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")
    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise ValueError(f"workspace not found for project: {project_id}")
    root = (ws_folder / project_folder_name(project.name)).resolve()
    root.mkdir(parents=True, exist_ok=True)

    rel = str(rel_path or "").strip()
    if not rel or rel.startswith("/") or rel.startswith("~"):
        raise ValueError("path must be a relative path inside the project folder")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes the project folder: {rel_path!r}")
    return target


__all__ = [
    "RUNNABLE_LANGUAGE_MAP",
    "runner_language",
    "is_runnable",
    "diff_versions",
    "resolve_project_save_path",
]
