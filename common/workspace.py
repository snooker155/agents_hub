"""
Workspace management utilities shared across the project.

Handles creation and management of per-task workspaces within the workspaces directory.
This module keeps backward-compatible helpers named with 'project' but exposes
preferred 'workspace' helpers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any
import uuid
import json

# Default workspaces root relative to project root
WORKSPACES_ROOT = Path(__file__).resolve().parents[1] / "workspaces"


def ensure_workspaces_dir() -> Path:
    """Ensure the workspaces root directory exists."""
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACES_ROOT


def create_project_folder(project_name: Optional[str] = None) -> Path:
    """
    Create a new workspace folder in the workspaces directory and initialize metadata.
    
    Args:
        project_name: Optional name for the workspace folder. If not provided,
                     generates a unique name using UUID.
    
    Returns:
        Path to the created workspace folder (absolute).
    """
    ensure_workspaces_dir()
    
    if project_name is None:
        project_name = str(uuid.uuid4())[:8]
    
    project_path = WORKSPACES_ROOT / project_name
    project_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize workspace metadata if not exists
    meta_path = project_path / ".workspace.json"
    if not meta_path.exists():
        default_meta = {
            "name": project_name,
            "created_at": str(uuid.uuid4()),  # Placeholder for actual time if needed
            "allowed_agents": ["swe_agent", "orchestrator"],
        }
        meta_path.write_text(json.dumps(default_meta, indent=2))

    return project_path.resolve()


def get_workspace_metadata(name: str) -> Dict[str, Any]:
    """Get metadata for a workspace."""
    folder = get_workspace_folder(name)
    if not folder:
        return {}
    meta_path = folder / ".workspace.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    return {}


def update_workspace_metadata(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update metadata for a workspace."""
    folder = get_workspace_folder(name)
    if not folder:
        return {}
    meta_path = folder / ".workspace.json"
    meta = get_workspace_metadata(name)
    meta.update(updates)
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


def get_project_folder(project_name: str) -> Optional[Path]:
    """
    Get the path to an existing workspace folder.
    
    Args:
        project_name: Name of the workspace folder.
    
    Returns:
        Path to the workspace folder if it exists, None otherwise.
    """
    project_path = WORKSPACES_ROOT / project_name
    if project_path.exists() and project_path.is_dir():
        return project_path.resolve()
    return None


def list_project_folders() -> list[Path]:
    """
    List all existing workspace folders.
    
    Returns:
        List of absolute paths to all workspace folders.
    """
    ensure_workspaces_dir()
    return [p for p in WORKSPACES_ROOT.iterdir() if p.is_dir()]


# -------- Preferred API (aliases) --------

def create_workspace_folder(name: Optional[str] = None) -> Path:
    return create_project_folder(name)


def get_workspace_folder(name: str) -> Optional[Path]:
    return get_project_folder(name)


def list_workspace_folders() -> list[Path]:
    return list_project_folders()
