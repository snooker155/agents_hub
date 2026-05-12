"""
Agents Hub API

Organized by domains:
- agents: Agent management (registry, connections, memory)
- tasks: Task management (creation, assignment, execution)
- factory: AI factory integration
- stats: System statistics and monitoring
- memory: Shared memory management
- workspaces: Workspace management
"""
import sys
from pathlib import Path as PathlibPath

# Ensure project root is on sys.path when running this file as a script
project_root = PathlibPath(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Ensure the backend dir is on sys.path so bare imports like
# `from routes import ...`, `from models import ...` work both when this
# file is run as a script and when launched via `uvicorn dashboard.backend.main:app`.
_backend_dir = PathlibPath(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import os

# Load .env into os.environ so RagConfig and other direct os.environ readers
# pick up saved settings on startup (pydantic BaseSettings reads .env into its
# own fields but does NOT populate os.environ).
from dotenv import load_dotenv
load_dotenv(project_root / ".env", override=False)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import route modules organized by domain
from routes import agents, tasks, flows, stats, memory, workspaces, tools, sessions, chat, nodes, external, projects, containers, messages
from routes import settings as settings_router

# Import settings for API key validation
from common.config import settings

# Initialize FastAPI app
app = FastAPI(
    title="Agents Hub",
    description="Multi-domain agent orchestration and task management",
    version="1.0.0"
)

# Startup event to validate OpenAI API key
@app.on_event("startup")
async def startup_event():
    """Validate API key, wire session broker, and launch the default orchestrator node."""
    import asyncio
    from common.session_broker import broker
    broker.set_loop(asyncio.get_running_loop())
    if not settings.openai_api_key:
        print("\n" + "="*70)
        print("WARNING: OPENAI_API_KEY is not set!")
        print("="*70)
        print("To use AI agents, set the OpenAI API key in one of these ways:")
        print("  1. Environment variable: export OPENAI_API_KEY='your-key-here'")
        print("  2. Create a .env file in the project root with: OPENAI_API_KEY='your-key-here'")
        print("  3. Set it directly before running this script")
        print("\nWithout the API key, agent tasks will fail.")
        print("="*70 + "\n")
    else:
        print("✓ OpenAI API key is configured")

    # Auto-start the default orchestrator node (runs in its own process)
    try:
        from agents.node_manager import ensure_default_node
        node_id = ensure_default_node()
        if node_id:
            print(f"✓ Default orchestrator node started  ({node_id[:8]})")
        else:
            print("✓ Orchestrator node already running")
    except Exception as e:
        print(f"⚠ Could not start default orchestrator node: {e}")

# Helper to locate orchestrator settings (moved under agents/state)
def get_orchestrator_settings_path() -> PathlibPath:
    """Return the path to orchestrator settings JSON under agents/state.

    Ensures the directory exists and initializes the file if missing.
    """
    path = project_root / "agents" / "state" / "orchestrator_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            path.write_text("{\"enabled\": false}", encoding="utf-8")
        except Exception:
            pass
    return path

# ============================================================================
# CORS Configuration
# ============================================================================
# CORS configuration (safe defaults for local dev; override with ALLOW_ORIGINS)
_env_allowed = os.getenv("ALLOW_ORIGINS", "").strip()

if _env_allowed == "*":
    # Allow any origin (use only for local dev / proxies)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_origin_regex=r".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    _default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    _origins = (
        [o.strip() for o in _env_allowed.split(",") if o.strip()]
        or _default_origins
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ============================================================================
# Root Endpoint
# ============================================================================
@app.get("/")
async def root():
    """Root endpoint providing API information."""
    return {
        "message": "Agents Hub API is running",
        "version": "1.0.0",
        "domains": [
            "agents",
            "tasks",
            "factory",
            "stats",
            "memory",
            "workspaces"
        ]
    }

# ============================================================================
# Include Domain-Based Routers
# ============================================================================
# Each router is organized by domain and has its own prefix and tags

# Agents domain: agent registry, connections, memory management
app.include_router(agents.router)

# Tasks domain: task creation, assignment, execution
app.include_router(tasks.router)

# Flows domain: user-defined factories / visual pipelines
app.include_router(flows.router)

# Stats domain: system statistics and monitoring
app.include_router(stats.router)

# Memory domain: shared memory management
app.include_router(memory.router)

# Workspaces domain: workspace management and tasks
app.include_router(workspaces.router)

# Tools domain: tools listing and exploration
app.include_router(tools.router)

# Sessions domain: process-level session contexts
app.include_router(sessions.router)

# Messages domain: individual agent run logs
app.include_router(messages.router)

# Chat domain: direct in-process agent conversation
app.include_router(chat.router)

# Nodes domain: long-running agent node management
app.include_router(nodes.router)

# External domain: token-authenticated access for exposed nodes
app.include_router(external.router)

# Projects domain: project management, repo, frontend/backend preview
app.include_router(projects.router)

# Containers domain: Docker image builds and container lifecycle
app.include_router(containers.router)

# Settings domain: LLM and application settings
app.include_router(settings_router.router)

# ============================================================================
# Entry Point
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parents[2]
    uvicorn.run(
        "main:app",
        port=8000,
        reload=True,
        reload_dirs=[
            str(_Path(__file__).parent),   # dashboard/backend
            str(_root / "agents"),
            str(_root / "common"),
            str(_root / "tools"),
            str(_root / "tasks"),
        ],
    )
