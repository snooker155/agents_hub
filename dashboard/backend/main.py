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
from routes import agent_import, agents, chats, connections as connections_router, ingest as ingest_router, context_refs, entity_chats, page_chat, tasks, flows, stats, memory, workspaces, tools, sessions, chat, nodes, external, projects, containers, messages, telegram, flow_entities, git, blender, marketplace, plan, stream, health, costs, replay, views, evals, playground, skills, weblogs, loops, teams, instances, mcp as mcp_router, notify as notify_router
from routes import a2a as a2a_router
from routes import auth as auth_router
from routes import run_groups as run_groups_router
from routes import run_state as run_state_router
from routes import settings as settings_router
from routes import models as models_router
from routes import ops as ops_router
from routes import deployment as deployment_router

# Settings: read at startup for the optional-feature checks below. The auth
# guard reads the live settings object through common.identity instead, so a
# mode changed in .env takes effect on the next restart without this import
# having pinned an old value.
from common.config import settings  # noqa: F401  (kept: imported by name elsewhere)


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

    from common.config import hub_role
    print(f"✓ Role: {hub_role()}"
          + ("  (launches go to the run queue for workers)" if hub_role() == "api" else ""))

    # The Telegram poller runs on exactly one replica: the supervisor holds
    # the ``telegram`` lease and starts the poller when it gets it, stops it
    # when it loses it (common/singletons.py). Configured-and-enabled is
    # re-read on every check, so the Connectors page still applies live.
    try:
        from common.singletons import supervisor as _supervisor, telegram_service
        _supervisor.add(telegram_service())
        await _supervisor.start()
        print("✓ Singleton supervisor started (telegram)")
    except Exception as e:
        print(f"⚠ Could not start the singleton supervisor: {e}")

    # This replica's row on the deployment map (common/members.py): registered
    # now, refreshed from a daemon thread with what the process is carrying.
    try:
        from common.members import MemberBeat
        from common.session_broker import broker as _broker

        def _load():
            from common import db as _dbm
            try:
                running = _dbm.get_conn().execute(
                    "SELECT COUNT(*) FROM runs WHERE status = 'running' AND host = ?",
                    (__import__("socket").gethostname(),)).fetchone()[0]
            except Exception:
                running = None
            return {"sse_clients": len(getattr(_broker, "_clients", {}) or {}),
                    "running_runs_on_host": running}

        app.state.member_beat = MemberBeat(hub_role(), capabilities={
            "http": True, "execution_modes": ["local", "docker"],
        }, load_fn=_load)
        app.state.member_beat.start()
        print("✓ Registered on the deployment map")
    except Exception as e:
        print(f"⚠ Could not register this replica: {e}")

    # Outbound webhook deliveries left in the outbox by an earlier process go
    # out as soon as this replica holds the ``outbox`` lease.
    try:
        from notify import outbound as _notify_outbound
        _notify_outbound.start()
    except Exception as e:
        print(f"⚠ Could not start the outbox drainer: {e}")

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

    # Start the cross-replica broker bridge (common/broker_bridge.py). A no-op
    # when AGENTS_HUB_BROKER_URL is unset, which is the default and what a
    # single-replica deployment wants; see docs/scaling.md.
    try:
        from common.broker_bridge import start_bridge
        await start_bridge()
    except Exception as e:
        print(f"⚠ Could not start broker bridge: {e}")

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
        from common.broker_bridge import stop_bridge
        await stop_bridge()
    except Exception:
        pass
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
        from common.singletons import supervisor as _supervisor
        await _supervisor.stop()
    except Exception:
        pass
    try:
        from notify import outbound as _notify_outbound
        _notify_outbound.shutdown()
    except Exception:
        pass
    beat = getattr(app.state, "member_beat", None)
    if beat is not None:
        try:
            beat.stop()
        except Exception:
            pass
    # Every role this replica held goes back to the pool at once, so a
    # restart is taken over by a sibling immediately, not after the TTL.
    try:
        from common import leases as _leases
        _leases.release_all()
    except Exception:
        pass
    try:
        from common import db as _db
        _db.close_pool()
    except Exception:
        pass


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
# One guard for all three AUTH_MODE postures (see common/identity.py and
# docs/identity.md). ``single`` — the default — lets everything through
# exactly as before any of this existed. ``token`` is the original shared
# ``AGENTS_HUB_API_TOKEN``: every /api request must present it via
# ``Authorization: Bearer <token>``, an ``X-Api-Token: <token>`` header, or a
# ``?token=<token>`` query parameter (the query form lets the browser's
# EventSource, which cannot set headers, authenticate the /api/stream SSE).
# ``multi`` resolves a session token to a named user and checks their global
# role and their membership of the workspace the request names.
#
# The decision itself lives in ``common.identity.authorize_request`` on top of
# the pure predicates in ``common.auth``, so the whole matrix is testable
# without a server; this function only translates the answer into a response.
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

# Connections domain: external agents that run on their own trigger and report
# here. Two routers on purpose — one manages connections and is operator-
# authenticated, the other is authenticated by a connection's own token and can
# only report runs. See EXTERNAL_CONNECTIONS.md.
app.include_router(connections_router.router)
app.include_router(ingest_router.router)

# MCP domain: external MCP servers attached per workspace as a group of tools.
# Beside the connectors in the sidebar, and the same direction: this hub reaches
# out to a server somebody else runs. See docs/mcp.md.
app.include_router(mcp_router.router)

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

# Costs domain: token/$ spend breakdowns and per-workspace budget caps
app.include_router(costs.router)

# Replay domain: re-run a recorded run and diff outputs (regression eval)
app.include_router(replay.router)

# Evals domain: batch replay across cases x models, scored by graders
app.include_router(evals.router)

# Playground domain: multi-agent simulation against a deterministic environment.
# Optional: off (PLAYGROUND_ENABLED=false) skips both the routes and the
# ``playground`` package import they would otherwise trigger. See
# docs/playground.md, "Turning the playground off".
from common.config import playground_enabled as _playground_enabled
if _playground_enabled():
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

# The conversations themselves: stored server-side, not in the browser
app.include_router(chats.router)

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

# A2A domain: agent cards and the JSON-RPC endpoint of the Agent2Agent protocol.
# Carries no prefix of its own: the well-known card path is fixed by the spec
# and sits at the site root, so this router spells out its own paths.
app.include_router(a2a_router.router)

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

# Ops domain: liveness, readiness and Prometheus metrics — /livez, /readyz,
# /metrics, unprefixed and open in every AUTH_MODE (see routes/ops.py).
app.include_router(ops_router.router)

# Deployment domain: the map of members, leases, queue and where everything
# runs (docs/deployment.md, "The deployment map").
app.include_router(deployment_router.router)

# Views domain: rich agent-generated views + their assets and per-user state
app.include_router(views.router)

# Notifications domain: outbound webhook/slack endpoints, alert rules, and the
# inbound task-filing webhook.
app.include_router(notify_router.router)

# Identity domain: the auth-mode probe, login/logout, users and workspace
# membership. Registered in every mode — ``GET /api/auth/mode`` is how the
# frontend learns there is nothing to render.
app.include_router(auth_router.router)

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
