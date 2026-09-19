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
import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path as PathlibPath
from typing import Any, Dict, List, Optional

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

# Seed first-run state (agents.json + default workspace) from bootstrap/ before
# any route imports trigger the registry cache.
from common.bootstrap import ensure_initial_state
ensure_initial_state()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# Import route modules organized by domain
from routes import agent_import, agents, context_refs, entity_chats, page_chat, tasks, flows, stats, memory, workspaces, tools, sessions, chat, nodes, external, projects, containers, messages, telegram, flow_entities, git, blender, marketplace, plan, stream, health, costs, replay, views, evals, playground, skills, weblogs, loops, teams, instances
from routes import settings as settings_router
from routes import models as models_router

# Settings, for the optional bearer token below
from common.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Background services live exactly as long as the app does.

    Startup wires up the broker, the Telegram poller, the plan scheduler, the
    external-state publisher and the run watchdog; everything after the yield
    stops them again. Each is started inside its own try: a connector that
    will not come up must not take the API down with it.

    The orchestrator node is intentionally NOT started here: orchestration runs
    only when the user starts an orchestrator node (Nodes UI / POST /api/nodes).
    """
    import asyncio
    from common.session_broker import broker
    broker.set_loop(asyncio.get_running_loop())

    # Apply the configured log level (Settings -> System -> Logging) to this
    # process. Uvicorn has already installed its handlers by now, so this only
    # moves the threshold they log at. The level is read for the workspace the
    # UI has selected, which is where the Settings page writes it.
    from common.logging_config import configure_logging_for_active_workspace
    print(f"✓ Log level: {configure_logging_for_active_workspace()}")

    # The orchestrator node is started on demand by the user, not at startup.

    # Auto-start the Telegram poller if it's been configured + enabled.
    try:
        from connectors.telegram.telegram_runner import service as _tg_service
        from connectors.telegram import telegram_store
        if telegram_store.is_enabled() and telegram_store.has_token():
            await _tg_service.start()
            if _tg_service.is_running():
                uname = _tg_service.status.get("bot_username") or "?"
                print(f"✓ Telegram poller started  (@{uname})")
            else:
                err = _tg_service.status.get("last_error") or "unknown"
                print(f"⚠ Telegram poller did not start: {err}")
    except Exception as e:
        print(f"⚠ Could not start Telegram poller: {e}")

    # Start the plan scheduler (fires due scheduled jobs / notifications).
    try:
        from plans.scheduler import scheduler as _plan_scheduler
        await _plan_scheduler.start()
        print("✓ Plan scheduler started")
    except Exception as e:
        print(f"⚠ Could not start plan scheduler: {e}")

    # Start the periodic external-state publisher (containers, node heartbeats,
    # log tails) — pushes snapshots over the single SSE stream so the UI never polls.
    try:
        from common.live_state import run_external_publisher
        app.state.external_publisher = asyncio.create_task(run_external_publisher())
        print("✓ External-state publisher started")
    except Exception as e:
        print(f"⚠ Could not start external-state publisher: {e}")

    # Start the run watchdog (fails runs stuck in 'pending' and runs whose
    # process died, so tasks never freeze waiting on a run that cannot finish).
    try:
        from managers.run_watchdog import watchdog as _run_watchdog
        await _run_watchdog.start()
        print("✓ Run watchdog started")
    except Exception as e:
        print(f"⚠ Could not start run watchdog: {e}")

    yield

    task = getattr(app.state, "external_publisher", None)
    if task:
        task.cancel()
    try:
        from plans.scheduler import scheduler as _plan_scheduler
        await _plan_scheduler.stop()
    except Exception:
        pass
    try:
        from managers.run_watchdog import watchdog as _run_watchdog
        await _run_watchdog.stop()
    except Exception:
        pass
    try:
        from connectors.telegram.telegram_runner import service as _tg_service
        await _tg_service.stop()
    except Exception:
        pass


# Initialize FastAPI app
app = FastAPI(
    title="Agents Hub",
    description="Multi-domain agent orchestration and task management",
    version="1.0.0",
    lifespan=lifespan,
)


# Helper to locate orchestrator settings (stored under .agents_hub)
def get_orchestrator_settings_path() -> PathlibPath:
    """Return the path to orchestrator settings JSON under .agents_hub.

    Ensures the directory exists and initializes the file if missing.
    """
    from common.paths import AGENTS_HUB_ROOT
    path = AGENTS_HUB_ROOT / "orchestrator_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            path.write_text("{\"enabled\": false}", encoding="utf-8")
        except Exception:
            pass
    return path

# ============================================================================
# Optional bearer-token authentication
# ============================================================================
# Off by default (settings.api_token == "") so the local-only workflow is
# unchanged. When a token is configured, every /api request must present it via
# ``Authorization: Bearer <token>``, ``X-Api-Token: <token>`` header, or a
# ``?token=<token>`` query parameter — the query form lets the browser's
# EventSource (which cannot set headers) authenticate the /api/stream SSE.
#
# Registered BEFORE the CORS middleware on purpose. Starlette's add_middleware
# inserts at the head of the list and the head is the outermost layer, so the
# *last* registration wraps every earlier one — registering this guard after
# CORS would put it outside CORSMiddleware, and its 401 would reach the browser
# with no Access-Control-Allow-Origin header. The browser then reports a CORS
# failure instead of the auth failure that actually happened.
async def _api_token_guard(request, call_next):
    from common.auth import is_authorized
    if not is_authorized(
        configured_token=settings.api_token,
        method=request.method,
        path=request.url.path,
        auth_header=request.headers.get("authorization"),
        x_api_token=request.headers.get("x-api-token"),
        query_token=request.query_params.get("token"),
    ):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API token"})
    return await call_next(request)


app.add_middleware(BaseHTTPMiddleware, dispatch=_api_token_guard)

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

# Agent import domain: bringing an agent in from its own git repository
app.include_router(agent_import.router)

# Marketplace domain: catalog of agents published across workspaces
app.include_router(marketplace.router)

# Tasks domain: task creation, assignment, execution
app.include_router(tasks.router)

# Plan domain: scheduled jobs (future notifications / agent tasks) + inbox
app.include_router(plan.router)

# Flows domain: user-defined factories / visual pipelines
app.include_router(flows.router)

# Flow entity registry: federated catalog of flow-usable nodes
app.include_router(flow_entities.router)

# Loops domain: a flow re-run until an agent judges the exit criterion met
app.include_router(loops.router)

# Teams domain: a bounded roster of agents that know each other and talk
app.include_router(teams.router)

# Stats domain: system statistics and monitoring
app.include_router(stats.router)

# Models domain: curated model catalog and per-model usage stats
app.include_router(models_router.router)

# Costs domain: token/$ spend breakdowns and per-workspace budget caps
app.include_router(costs.router)

# Replay domain: re-run a recorded run and diff outputs (regression eval)
app.include_router(replay.router)

# Evals domain: batch replay across cases x models, scored by graders
app.include_router(evals.router)

# Playground domain: multi-agent simulation against a deterministic environment
app.include_router(playground.router)

# Memory domain: shared memory management
app.include_router(memory.router)

# Skills domain: workspace skill catalog, publishing, install-onto-agent
app.include_router(skills.router)

# Web log domain: recorded web_search / fetch_url calls and their responses
app.include_router(weblogs.router)

# Workspaces domain: workspace management and tasks
app.include_router(workspaces.router)

# Tools domain: tools listing and exploration
app.include_router(tools.router)

# Sessions domain: process-level session contexts
app.include_router(sessions.router)

# Messages domain: individual agent run logs
app.include_router(messages.router)

# Live agent copies: what is running right now, and how to write to one.
app.include_router(instances.router)

# Chat domain: direct in-process agent conversation
app.include_router(chat.router)

# What the chat composer can attach besides a file: the entity picker's catalog
app.include_router(context_refs.router)

# The page chat: one assistant, any page, about the records that page is showing
app.include_router(page_chat.router)

# Session history shared by every entity build chat: list past threads, reopen one
app.include_router(entity_chats.router)

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

# Telegram domain: bot config, bindings, and outbound message proxy
app.include_router(telegram.router)

# Git connectors domain: GitHub/GitLab tokens, repo browsing
app.include_router(git.router)

# Blender geometry connector: binary path / mode, and the running engines.
app.include_router(blender.router)

# Real-time domain: single multiplexed SSE stream for the whole UI
app.include_router(stream.router)

# Health domain: liveness, store counts, background-service status, state sizes
app.include_router(health.router)

# Views domain: rich agent-generated views + their assets and per-user state
app.include_router(views.router)

# ============================================================================
# Entry Point
# ============================================================================
# The source trees a reloader has to watch. The backend imports from all of
# them, so watching only its own directory would miss most edits.
RELOAD_DIRS = [
    str(_backend_dir),
    *(str(project_root / name) for name in
      ("agents", "common", "tools", "tasks", "chat", "flow")),
]


def uvicorn_options(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    """Options for running this module directly: `python main.py [--reload]`.

    The reloader is opt-in. It restarts the process on any write under the
    watched trees, which is what you want while editing and never what you want
    anywhere else: the backend owns singletons (the plan scheduler, the run
    watchdog, the Telegram poller) that a restart interrupts mid-flight.
    """
    parser = argparse.ArgumentParser(description="Run the Agents Hub API.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to.")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on.")
    parser.add_argument("--reload", action="store_true",
                        help="Restart on source changes (development only).")
    args = parser.parse_args(argv)

    options: Dict[str, Any] = {"host": args.host, "port": args.port, "reload": args.reload}
    if args.reload:
        options["reload_dirs"] = RELOAD_DIRS
    return options


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", **uvicorn_options())
