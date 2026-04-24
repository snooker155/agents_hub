from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_HUB_ROOT = PROJECT_ROOT / ".agents_hub"

WORKSPACES_ROOT = AGENTS_HUB_ROOT / "workspaces"
TASKS_FILE = AGENTS_HUB_ROOT / "tasks.json"
PROJECTS_FILE = AGENTS_HUB_ROOT / "projects.json"
SHARED_MEMORY_FILE = AGENTS_HUB_ROOT / "shared_memory.json"
AGENTS_FILE = AGENTS_HUB_ROOT / "agents.json"


def ensure_agents_hub_root() -> Path:
    AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
    return AGENTS_HUB_ROOT


def ensure_workspaces_root() -> Path:
    ensure_agents_hub_root()
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACES_ROOT
