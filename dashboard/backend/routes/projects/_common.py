"""Shared state and helpers for the projects route modules."""
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[4]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from projects.models import Project
from typing import Optional

from projects.graph_store import ProjectGraphStore
from projects.storage import ProjectStore  # noqa: F401
from workspace import get_workspace_folder, project_folder_name
from common.paths import PROJECTS_FILE  # noqa: F401


def package():
    """The ``routes.projects`` package module, whichever dotted path loaded it.

    Names tests patch on the package (``_store``, ``_PREVIEW_CHARS``) are read
    through it at call time.
    """
    return sys.modules[__name__.rsplit(".", 1)[0]]


def store():
    return package()._store


def _project_to_dict(project: Project) -> dict:
    if hasattr(project, "model_dump"):
        return project.model_dump()
    return project.dict()


def _task_to_dict(task) -> dict:
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data


_graph_store = ProjectGraphStore()


def _project_root_path(project) -> Optional[Path]:
    """Return the resolved project subfolder path, or None if the workspace doesn't exist."""
    ws = get_workspace_folder(project.workspace)
    if ws is None:
        return None
    folder = project_folder_name(project.name)
    root = (ws / folder).resolve()
    return root if root.exists() else None
