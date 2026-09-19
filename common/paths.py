from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The state root defaults to ``<repo>/.agents_hub`` but can be redirected with the
# ``AGENTS_HUB_ROOT`` env var — used to run an isolated second instance and to
# point the test suite at a throwaway directory so it never touches real state.
_ENV_ROOT = os.environ.get("AGENTS_HUB_ROOT")
AGENTS_HUB_ROOT = Path(_ENV_ROOT).expanduser().resolve() if _ENV_ROOT else PROJECT_ROOT / ".agents_hub"

# Single SQLite database holding the concurrency-critical stores: run records
# (+ structured payloads), tasks (+ activity/results/routing sidecars),
# session contexts (+ pending continuations) and node records. WAL mode makes
# it safe for the backend process, agent subprocesses and node workers to
# read/write concurrently. Legacy JSON stores are migrated in on first open
# (see common.db_migrate) and renamed to *.migrated.
DB_FILE = AGENTS_HUB_ROOT / "agents_hub.db"

WORKSPACES_ROOT = AGENTS_HUB_ROOT / "workspaces"
# Central per-workspace metadata (settings, allowed agents/flows, env_vars,
# model_override, default_chat_agent). One file keyed by workspace name, instead
# of a .workspace.json inside each workspace folder. The folders still hold
# .logs/, .plans/, knowledge/ and project subfolders.
WORKSPACES_META_FILE = AGENTS_HUB_ROOT / "workspaces.json"
TASKS_FILE = AGENTS_HUB_ROOT / "tasks.json"
PROJECTS_FILE = AGENTS_HUB_ROOT / "projects.json"
PROCEDURES_FILE = AGENTS_HUB_ROOT / "procedures.json"
# Append-only log of every web_search / fetch_url call: the request, the text
# handed back to the agent, and the security flags raised against it
# (tools/web_log.py). Capped and trimmed in place, newest kept.
WEB_LOG_FILE = AGENTS_HUB_ROOT / "web_requests.jsonl"
SHARED_MEMORY_FILE = AGENTS_HUB_ROOT / "shared_memory.json"
EPISODES_DIR = AGENTS_HUB_ROOT / "episodes"
GRAPHS_DIR = AGENTS_HUB_ROOT / "graphs"
EXTRACTIONS_DIR = AGENTS_HUB_ROOT / "extractions"
AGENTS_FILE = AGENTS_HUB_ROOT / "agents.json"
# User/agent-created flow entities (data records, analogous to agents.json).
# Code-defined entities live as files under flow/entities/<category>/.
FLOW_ENTITIES_FILE = AGENTS_HUB_ROOT / "flow_entities.json"
# Scheduled jobs (Plan page) and the user notification inbox.
PLANS_FILE = AGENTS_HUB_ROOT / "plans.json"
NOTIFICATIONS_FILE = AGENTS_HUB_ROOT / "notifications.json"
# Curated model catalog (Models page): per-provider list of available models,
# which are enabled, the default per provider, and per-model pricing used to
# estimate cost from recorded token usage.
MODELS_FILE = AGENTS_HUB_ROOT / "models.json"
# User-defined custom model backends (Settings page): named OpenAI-compatible (or
# other-adapter) endpoints with their base URL, API key, optional headers, and
# adapter kind. Each becomes a first-class "provider" in the model catalog.
CUSTOM_PROVIDERS_FILE = AGENTS_HUB_ROOT / "custom_providers.json"
# Saved project structure graphs (architecture / process views), keyed by
# "<project_id>:<view>". Holds hand-edited and AI-generated graphs so they
# survive a deterministic rebuild.
PROJECT_GRAPHS_FILE = AGENTS_HUB_ROOT / "project_graphs.json"

# Build-chat transcripts for service entities that have a chat of their own
# (scenarios, loops), keyed by "<kind>:<entity_id>". The project graph keeps its
# own file because the conversation lives next to the graph it builds.
ENTITY_CHATS_FILE = AGENTS_HUB_ROOT / "entity_chats.json"


def pool_episodes_file(pool_id: str) -> Path:
    """Return the episodes.json path for a specific shared-memory pool."""
    return EPISODES_DIR / f"{pool_id}.json"


def pool_graph_file(pool_id: str) -> Path:
    """Return the graph.json path for a specific shared-memory pool."""
    return GRAPHS_DIR / f"{pool_id}.json"


def pool_extractions_file(pool_id: str) -> Path:
    """Return the pending-extractions.json path for a specific shared-memory pool."""
    return EXTRACTIONS_DIR / f"{pool_id}.json"


def workspace_knowledge_dir(workspace: str) -> Path:
    """Return (and ensure) the knowledge/ folder inside a workspace.

    All RAG source files for any pool live here as a flat list.
    Which files are indexed into which pool is tracked in SharedMemory.rag_files.
    """
    p = WORKSPACES_ROOT / workspace / "knowledge"
    p.mkdir(parents=True, exist_ok=True)
    return p


# Agents imported from external git repositories (see agents.importer). Each
# import gets its own subfolder holding the clone, so the repo's own code, its
# manifest and its Dockerfile stay together and can be re-inspected later.
IMPORTED_AGENTS_DIR = AGENTS_HUB_ROOT / "imported_agents"


def imported_agent_dir(agent_id: str) -> Path:
    """Return the clone directory for an imported agent (not created here)."""
    return IMPORTED_AGENTS_DIR / Path(agent_id).name


# Rich views produced by agents that are not tied to a workspace (e.g. a chat
# with no active workspace) live here; workspace-scoped views live in
# ``WORKSPACES_ROOT/<ws>/.views`` so they travel with the workspace.
VIEWS_ROOT = AGENTS_HUB_ROOT / "views"


def workspace_views_dir(workspace: Optional[str]) -> Path:
    """Return the ``.views`` root for a workspace, or the global views root.

    ``workspace`` is a bare workspace name; ``None``/empty selects the global
    root. The directory is created on demand by the view store, not here.
    """
    name = (workspace or "").strip()
    if name:
        return WORKSPACES_ROOT / Path(name).name / ".views"
    return VIEWS_ROOT


def ensure_agents_hub_root() -> Path:
    AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
    return AGENTS_HUB_ROOT


def ensure_workspaces_root() -> Path:
    ensure_agents_hub_root()
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACES_ROOT
