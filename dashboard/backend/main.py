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
import logging
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
from routes import connectors as connectors_router
from routes import trackers as trackers_router
from routes import agent_import, agents, chats, context_refs, entity_chats, tasks, flows, stats, memory, workspaces, tools, sessions, chat, projects, containers, messages, flow_entities, git, marketplace, plan, stream, health, replay, views, skills, weblogs, loops, teams, instances, notify as notify_router
from routes import first_run as first_run_router
from routes import auth as auth_router
from routes import secrets as secrets_router
from routes import run_groups as run_groups_router
from routes import run_state as run_state_router
from routes import settings as settings_router
from routes import models as models_router
from routes import ops as ops_router
from routes import system as system_router
from routes import demo as demo_router

# Settings: read at startup for the optional-feature checks below. The auth
# guard reads the live settings object through common.identity instead, so a
# mode changed in .env takes effect on the next restart without this import
# having pinned an old value.
from common.config import settings  # noqa: F401  (kept: imported by name elsewhere)

log = logging.getLogger("dashboard.backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Background services live exactly as long as the app does.

    Startup wires up the broker, the plan scheduler, the
    external-state publisher and the run watchdog; everything after the yield
    stops them again. Each is started inside its own try: a connector that
    will not come up must not take the API down with it.

    The orchestrator is intentionally NOT started here: orchestration runs only
    when the user starts an orchestrator instance that takes tasks (the
    Orchestrator page, or POST /api/instances with take_tasks).
    """
    import asyncio
    from common.session_broker import broker
    broker.set_loop(asyncio.get_running_loop())

    # Apply the configured log level (Settings -> System -> Logging) to this
    # process. Uvicorn has already installed its handlers by now, so this only
    # moves the threshold they log at. The level is read for the workspace the
    # UI has selected, which is where the Settings page writes it.
    from common.logging_config import configure_logging_for_active_workspace
    log.info(f"✓ Log level: {configure_logging_for_active_workspace()}")

    # The orchestrator node is started on demand by the user, not at startup.

    # The singleton supervisor (common/singletons.py) runs the background
    # services that must exist exactly once in the process.
    try:
        from common.singletons import supervisor as _supervisor
        await _supervisor.start()
        log.info("✓ Singleton supervisor started")
    except Exception as e:  # noqa: BLE001 - startup of this part is optional, the hub must still boot
        log.warning(f"⚠ Could not start the singleton supervisor: {e}")

    # Start the plan scheduler (fires due scheduled jobs / notifications).
    try:
        from plans.scheduler import scheduler as _plan_scheduler
        await _plan_scheduler.start()
        log.info("✓ Plan scheduler started")
    except Exception as e:  # noqa: BLE001 - startup of this part is optional, the hub must still boot
        log.warning(f"⚠ Could not start plan scheduler: {e}")

    # Start the periodic external-state publisher (containers, node heartbeats,
    # log tails) — pushes snapshots over the single SSE stream so the UI never polls.
    try:
        from common.live_state import run_external_publisher
        app.state.external_publisher = asyncio.create_task(run_external_publisher())
        log.info("✓ External-state publisher started")
    except Exception as e:  # noqa: BLE001 - startup of this part is optional, the hub must still boot
        log.warning(f"⚠ Could not start external-state publisher: {e}")

    # Start the run watchdog (fails runs stuck in 'pending' and runs whose
    # process died, so tasks never freeze waiting on a run that cannot finish).
    try:
        from managers.run_watchdog import watchdog as _run_watchdog
        await _run_watchdog.start()
        log.info("✓ Run watchdog started")
    except Exception as e:  # noqa: BLE001 - startup of this part is optional, the hub must still boot
        log.warning(f"⚠ Could not start run watchdog: {e}")




    yield

    task = getattr(app.state, "external_publisher", None)
    if task:
        task.cancel()
    try:
        from plans.scheduler import scheduler as _plan_scheduler
        await _plan_scheduler.stop()
    except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
        log.debug("_load: best-effort step failed", exc_info=True)
    try:
        from managers.run_watchdog import watchdog as _run_watchdog
        await _run_watchdog.stop()
    except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
        log.debug("_load: best-effort step failed", exc_info=True)
    try:
        from common.singletons import supervisor as _supervisor
        await _supervisor.stop()
    except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
        log.debug("_load: best-effort step failed", exc_info=True)
    try:
        from common import leases as _leases
        _leases.release_all()
    except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
        log.debug("_load: best-effort step failed", exc_info=True)


# Initialize FastAPI app
app = FastAPI(
    title="Agents Hub",
    description="Multi-domain agent orchestration and task management",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================================
# Identity: authentication and authorization
# ============================================================================
# One operator, no login (see common/identity.py): every request is allowed
# and acts as the local operator. ``common.identity.authorize_request`` is
# where that is decided; this function puts the principal on the request and
# binds the current user for the stores that stamp an owner on new records.
#
# Registered BEFORE the CORS middleware on purpose. Starlette's add_middleware
# inserts at the head of the list and the head is the outermost layer, so the
# *last* registration wraps every earlier one — registering this guard after
# CORS would put it outside CORSMiddleware, and its 401 would reach the browser
# with no Access-Control-Allow-Origin header. The browser then reports a CORS
# failure instead of the auth failure that actually happened.
async def _api_token_guard(request, call_next):
    from common import identity

    allowed, principal = identity.authorize_request(request)
    if not allowed:
        from fastapi.responses import JSONResponse
        if principal is None:
            # Unchanged from before identity shipped: one ``detail`` string,
            # whether the request carried no credential, a wrong token or an
            # expired session. Which of the three it was is not information an
            # unauthenticated caller has earned.
            return JSONResponse(status_code=401,
                                content={"detail": "Invalid or missing API token"})
        # Authenticated, but not for this. 403 rather than 401 on purpose: the
        # browser treats a 401 as "your session is gone" and signs itself out,
        # so answering a workspace the caller simply is not a member of with
        # 401 would log them out of the whole app.
        return JSONResponse(status_code=403,
                            content={"detail": "Not a member of this workspace"})

    # Routes read the principal off the request; the stores that stamp an owner
    # onto a new record read it off a contextvar instead, so they need no
    # signature change (common.identity.current_user_id).
    request.state.principal = principal

    token = identity.set_current_user(principal.id if principal else None)
    try:
        return await call_next(request)
    finally:
        identity.reset_current_user(token)


# Optimistic concurrency for agent edits (agents/revision.py): an If-Match or
# expected_version that names an older definition is a 409 before the route
# runs, and reads and writes of an agent carry its definition hash as ETag.
# Registered before the guard so it sits inside it: only an authorised
# request is ever checked or tagged.
from agents.revision import AgentRevisionMiddleware
app.add_middleware(AgentRevisionMiddleware)

app.add_middleware(BaseHTTPMiddleware, dispatch=_api_token_guard)


# A workspace name taken from a request that is not one ordinary path
# component (see workspace.storage.create_workspace_folder). Every route that
# touches a workspace by name goes through that function, so one handler
# turns the refusal into a 400 instead of each route repeating the check.
from workspace import InvalidWorkspaceName as _InvalidWorkspaceName


@app.exception_handler(_InvalidWorkspaceName)
async def _invalid_workspace_name(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ============================================================================
# CORS Configuration
# ============================================================================
# CORS configuration (safe defaults for local dev; override with ALLOW_ORIGINS)
_DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://0.0.0.0:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def cors_options(env_value: Optional[str]) -> dict:
    """The CORSMiddleware keyword arguments for an ``ALLOW_ORIGINS`` value.

    ``*`` allows any origin without credentials: browsers refuse a wildcard
    with credentials anyway, and echoing any origin back with credentials
    (what a catch-all regex would do) lets every site on the web call the API
    as the signed-in person. The doctor's ``cors`` check flags ``*`` in
    ``multi`` mode. A comma-separated list, or the local dev defaults when
    empty, keeps credentials and the loopback regex.
    """
    value = (env_value or "").strip()
    if value == "*":
        return {"allow_origins": ["*"], "allow_credentials": False,
                "allow_methods": ["*"], "allow_headers": ["*"]}
    origins = [o.strip() for o in value.split(",") if o.strip()] or list(_DEFAULT_ORIGINS)
    return {"allow_origins": origins,
            "allow_origin_regex": r"http://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?",
            "allow_credentials": True,
            "allow_methods": ["*"], "allow_headers": ["*"]}


app.add_middleware(CORSMiddleware, **cors_options(os.getenv("ALLOW_ORIGINS", "")))


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

# Run groups: flows, loops, teams and task containers behind one interface
app.include_router(run_groups_router.router)

# Run state: the write surface a run container uses instead of opening the
# database itself, when AGENT_RUN_STATE_TRANSPORT=http (see docs/containers.md)
app.include_router(run_state_router.router)

# Teams domain: a bounded roster of agents that know each other and talk
app.include_router(teams.router)

# Stats domain: system statistics and monitoring
app.include_router(stats.router)

# Models domain: curated model catalog and per-model usage stats
app.include_router(models_router.router)

# Costs domain: token/$ spend breakdowns


# Terminal: a shell in a run's or a service replica's container, over a
# ticketed WebSocket. See docs/terminal.md.
from routes import terminal as terminal_router
app.include_router(terminal_router.router)

# Replay domain: re-run a recorded run and diff outputs (regression eval)
app.include_router(replay.router)

# Memory domain: shared memory management
app.include_router(memory.router)

# Skills domain: workspace skill catalog, publishing, install-onto-agent
app.include_router(skills.router)

# Web log domain: recorded fetch_url calls and their responses
app.include_router(weblogs.router)

# Workspaces domain: workspace management and tasks
app.include_router(workspaces.router)

# Tools domain: tools listing and exploration
app.include_router(tools.router)

# Sessions domain: process-level session contexts
app.include_router(sessions.router)

# Messages domain: individual agent run logs
app.include_router(messages.router)
# /api/runs/{id}/agent-version and rollback-agent: the agent version a run ran
# and a rollback to it (routes/messages.py).
app.include_router(messages.runs_router)

# Live agent copies: what is running right now, and how to write to one.
app.include_router(instances.router)

# Chat domain: direct in-process agent conversation
app.include_router(chat.router)

# The conversations themselves: stored server-side, not in the browser
app.include_router(chats.router)

# What the chat composer can attach besides a file: the entity picker's catalog
app.include_router(context_refs.router)

# The first run: the install's own setup in the browser, once (common/first_run.py)
app.include_router(first_run_router.router)

# Session history shared by every entity build chat: list past threads, reopen one
app.include_router(entity_chats.router)


# The agent loop's policies (fourth-cycle stage 2): per-tool permission
# policy, task outcomes graded against a rubric, messages steering a running
# turn, guardrails, per-agent loop settings (fallback models, output schema,
# tool search, compaction) and the version history of memory pools.
from routes import (
    tool_policy as tool_policy_router,
    outcomes as outcomes_router,
    steering as steering_router,
    guardrails as guardrails_router,
    agent_loop_settings as agent_loop_settings_router,
    memory_versions as memory_versions_router,
    memory_consolidation as memory_consolidation_router,
)
for _loop_router in (tool_policy_router, outcomes_router, steering_router, guardrails_router,
                     agent_loop_settings_router, memory_versions_router, memory_consolidation_router):
    app.include_router(_loop_router.router)

# A tool call waiting for a person inside a chat turn: the chat's Approve and
# Deny, and the waiting run's side under /api/run-state (docs/hooks.md).
from routes import tool_approvals as tool_approvals_router
app.include_router(tool_approvals_router.router)

# An agent's own domain lists for fetch_url
# (tools/web.py), on top of the workspace's and the global ones.
from routes import agent_web_domains as agent_web_domains_router
app.include_router(agent_web_domains_router.router)

# Files a workspace keeps by id (chat, memory and tasks reuse them).
from routes import files as files_router
app.include_router(files_router.router)


# Docs domain: a corpus document (the changelog) for the in-app Docs page.
from routes import docs as docs_router
app.include_router(docs_router.router)


# Projects domain: project management, repo, frontend/backend preview
app.include_router(projects.router)

# Containers domain: Docker image builds and container lifecycle
app.include_router(containers.router)

# Settings domain: LLM and application settings
app.include_router(settings_router.router)

# Connectors: the connector catalog (Jira and GitLab), and the Jira tracker
app.include_router(connectors_router.router)
app.include_router(trackers_router.router)

# Git connector domain: the GitLab token, repo browsing
app.include_router(git.router)

# Real-time domain: single multiplexed SSE stream for the whole UI
app.include_router(stream.router)

# Health domain: liveness, store counts, background-service status, state sizes
app.include_router(health.router)

# Ops domain: liveness, readiness and Prometheus metrics — /livez, /readyz,
# /metrics, unprefixed and open in every AUTH_MODE (see routes/ops.py).
app.include_router(ops_router.router)

# The running release and its newest schema migration (ah version).
app.include_router(system_router.router)

# Demo workspace domain: present or not, add or remove (docs/demo.md).
app.include_router(demo_router.router)

# Views domain: rich agent-generated views + their assets and per-user state
app.include_router(views.router)

# Notifications domain: the alert rules that raise in-app notifications.
app.include_router(notify_router.router)

# Identity domain: the auth-mode probe the frontend bootstraps from. There is
# one local operator and nothing to sign in to.
app.include_router(auth_router.router)

app.include_router(secrets_router.router)

# An agent's default outcome rubric (tasks/outcome.py).
from routes import agent_outcome as agent_outcome_router
app.include_router(agent_outcome_router.router)

# Agent templates: one ready agent to start from in the create wizard
# (docs/agent-templates.md, ``agents/templates/``).
from routes import agent_templates as agent_templates_router
app.include_router(agent_templates_router.router)

# ============================================================================
# Entry Point
# ============================================================================
# The source trees a reloader has to watch. The backend imports from all of
# them, so watching only its own directory would miss most edits.
RELOAD_DIRS = [
    str(_backend_dir),
    # Every package the backend imports from that holds code only (no state
    # files written at runtime, which would restart it in a loop). A package
    # left out keeps running its old code after an edit.
    *(str(project_root / name) for name in
      ("agents", "common", "tools", "tasks", "chat", "flow", "providers",
       "instances", "managers", "workspace", "reasoning", "memory", "connectors",
       "guardrails", "loops", "notify", "plans",
       "sandbox", "teams", "views", "declarative", "files", "projects")),
]
#: How long a reload waits for open connections before closing them.
RELOAD_GRACE_SECONDS = 5


def uvicorn_options(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    """Options for running this module directly: `python main.py [--reload]`.

    The reloader is opt-in. It restarts the process on any write under the
    watched trees, which is what you want while editing and never what you want
    anywhere else: the backend owns singletons (the plan scheduler, the run
    watchdog) that a restart interrupts mid-flight.
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
        # An open stream (a chat) would hold the old
        # process forever: uvicorn waits for every connection to close before
        # the new code starts. The browser reconnects to the new one.
        options["timeout_graceful_shutdown"] = RELOAD_GRACE_SECONDS
    return options


if __name__ == "__main__":
    import uvicorn

    options = uvicorn_options()
    # The reloader needs an import string. Without it, hand over the app this
    # run has already built: "main:app" would execute this file a second time.
    uvicorn.run("main:app" if options["reload"] else app, **options)
