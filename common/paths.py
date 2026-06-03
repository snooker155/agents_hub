from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_HUB_ROOT = PROJECT_ROOT / ".agents_hub"

WORKSPACES_ROOT = AGENTS_HUB_ROOT / "workspaces"
TASKS_FILE = AGENTS_HUB_ROOT / "tasks.json"
PROJECTS_FILE = AGENTS_HUB_ROOT / "projects.json"
PROCEDURES_FILE = AGENTS_HUB_ROOT / "procedures.json"
SHARED_MEMORY_FILE = AGENTS_HUB_ROOT / "shared_memory.json"
EPISODES_DIR = AGENTS_HUB_ROOT / "episodes"
GRAPHS_DIR = AGENTS_HUB_ROOT / "graphs"
AGENTS_FILE = AGENTS_HUB_ROOT / "agents.json"


def pool_episodes_file(pool_id: str) -> Path:
    """Return the episodes.json path for a specific shared-memory pool."""
    return EPISODES_DIR / f"{pool_id}.json"


def pool_graph_file(pool_id: str) -> Path:
    """Return the graph.json path for a specific shared-memory pool."""
    return GRAPHS_DIR / f"{pool_id}.json"


def workspace_knowledge_dir(workspace: str) -> Path:
    """Return (and ensure) the knowledge/ folder inside a workspace.

    All RAG source files for any pool live here as a flat list.
    Which files are indexed into which pool is tracked in SharedMemory.rag_files.
    """
    p = WORKSPACES_ROOT / workspace / "knowledge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_agents_hub_root() -> Path:
    AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
    return AGENTS_HUB_ROOT


def ensure_workspaces_root() -> Path:
    ensure_agents_hub_root()
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACES_ROOT
