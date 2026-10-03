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
from routes import channels as channels_router
from routes import connectors as connectors_router
from routes import slack as slack_router
from routes import teams_channel as teams_channel_router
from routes import trackers as trackers_router
from routes import google as google_router
from routes import databases as databases_router
from routes import agent_import, agents, chats, connections as connections_router, ingest as ingest_router, context_refs, entity_chats, page_chat, tasks, flows, stats, memory, workspaces, tools, sessions, chat, external, projects, containers, messages, telegram, flow_entities, git, blender, marketplace, plan, stream, health, costs, replay, views, evals, playground, skills, weblogs, loops, teams, instances, mcp as mcp_router, notify as notify_router
from routes import a2a as a2a_router
from routes import auth as auth_router
from routes import oidc as oidc_router
from routes import groups as groups_router
from routes import scim as scim_router
from routes import audit as audit_router
from routes import account as account_router
from routes import secrets as secrets_router
from routes import github_app as github_app_router
from routes import run_groups as run_groups_router
from routes import run_state as run_state_router
from routes import settings as settings_router
from routes import models as models_router
from routes import ops as ops_router
from routes import deployment as deployment_router
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

    Startup wires up the broker, the Telegram poller, the plan scheduler, the
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

    from common.config import hub_role
    log.info(f"✓ Role: {hub_role()}"
             + ("  (launches go to the run queue for workers)" if hub_role() == "api" else ""))

    # The Telegram poller runs on exactly one replica: the supervisor holds
    # the ``telegram`` lease and starts the poller when it gets it, stops it
    # when it loses it (common/singletons.py). Configured-and-enabled is
    # re-read on every check, so the Connectors page still applies live.
    try:
        from common.singletons import supervisor as _supervisor, telegram_service
        _supervisor.add(telegram_service())
        # Every registered chat channel (connectors/channels/registry.py) is a
        # leased service of the same shape as the Telegram poller.
        try:
            from connectors.channels import registry as _channels
            from connectors.channels.service import leased_service as _leased
            for _spec in _channels.all_channels():
                if _spec.has_loop:
                    _supervisor.add(_leased(_spec.service))
        except Exception as e:  # noqa: BLE001 - channels are optional
            log.warning(f"⚠ Chat channels not registered: {e}")
        await _supervisor.start()
        log.info("✓ Singleton supervisor started (telegram, channels, online_evals)")
    except Exception as e:
        log.warning(f"⚠ Could not start the singleton supervisor: {e}")

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
        log.info("✓ Registered on the deployment map")
    except Exception as e:
        log.warning(f"⚠ Could not register this replica: {e}")

    # Outbound webhook deliveries left in the outbox by an earlier process go
    # out as soon as this replica holds the ``outbox`` lease.
    try:
        from notify import outbound as _notify_outbound
        _notify_outbound.start()
    except Exception as e:
        log.warning(f"⚠ Could not start the outbox drainer: {e}")

    # Start the plan scheduler (fires due scheduled jobs / notifications).
    try:
        from plans.scheduler import scheduler as _plan_scheduler
        await _plan_scheduler.start()
        log.info("✓ Plan scheduler started")
    except Exception as e:
        log.warning(f"⚠ Could not start plan scheduler: {e}")

    # Start the watcher runner (polls mailboxes and HTTP resources, wakes the
    # proactive agents listening; one replica through the "watchers" lease).
    try:
        from watchers.runner import runner as _watcher_runner
        await _watcher_runner.start()
        log.info("✓ Watcher runner started")
    except Exception as e:  # noqa: BLE001 - watchers are optional, the app still serves
        log.warning(f"⚠ Could not start the watcher runner: {e}")

    # Start the periodic external-state publisher (containers, node heartbeats,
    # log tails) — pushes snapshots over the single SSE stream so the UI never polls.
    try:
        from common.live_state import run_external_publisher
        app.state.external_publisher = asyncio.create_task(run_external_publisher())
        log.info("✓ External-state publisher started")
    except Exception as e:
        log.warning(f"⚠ Could not start external-state publisher: {e}")

    # Start the cross-replica broker bridge (common/broker_bridge.py). A no-op
    # when AGENTS_HUB_BROKER_URL is unset, which is the default and what a
    # single-replica deployment wants; see docs/scaling.md.
    try:
        from common.broker_bridge import start_bridge
        await start_bridge()
    except Exception as e:
        log.warning(f"⚠ Could not start broker bridge: {e}")

    # Start the run watchdog (fails runs stuck in 'pending' and runs whose
    # process died, so tasks never freeze waiting on a run that cannot finish).
    try:
        from managers.run_watchdog import watchdog as _run_watchdog
        await _run_watchdog.start()
        log.info("✓ Run watchdog started")
    except Exception as e:
        log.warning(f"⚠ Could not start run watchdog: {e}")

    # The egress proxy for environments with a limited network
    # (environments/egress.py); a no-op unless AGENTS_HUB_EGRESS_PROXY is on.
    try:
        from environments import egress as _egress
        if _egress.start_background() is not None:
            log.info("✓ Egress proxy started")
    except Exception as e:
        log.warning(f"⚠ Could not start the egress proxy: {e}")

    # The service supervisor (services/supervisor.py): keeps every service's
    # replicas at its desired state, and the default runner warm, so a chat
    # turn never waits for a process to boot. A worker serves no chat and
    # starts no replicas.
    if hub_role() != "worker":
        try:
            from services.supervisor import supervisor as _service_supervisor
            await _service_supervisor.start()
            log.info("✓ Service supervisor started")
        except Exception as e:  # noqa: BLE001 - the hub starts without it; services stay down
            log.warning(f"⚠ Could not start the service supervisor: {e}")

    # The deployment supervisor (deployments/supervisor.py): keeps a project's
    # deployed services alive and pauses a crash loop. See
    # docs/project-deployments.md.
    if hub_role() != "worker":
        try:
            from deployments.supervisor import supervisor as _deployment_supervisor
            await _deployment_supervisor.start()
            log.info("✓ Deployment supervisor started")
        except Exception as e:  # noqa: BLE001 - the hub starts without it; deployments stay down
            log.warning(f"⚠ Could not start the deployment supervisor: {e}")

    yield

    try:
        from deployments.supervisor import supervisor as _deployment_supervisor
        await _deployment_supervisor.stop()
    except Exception:  # noqa: BLE001 - shutting down anyway
        log.debug("could not stop the deployment supervisor", exc_info=True)

    try:
        from services.supervisor import supervisor as _service_supervisor
        await _service_supervisor.stop()
    except Exception:  # noqa: BLE001 - shutting down anyway
        log.debug("could not stop the service supervisor", exc_info=True)

    try:
        from environments import egress as _egress
        _egress.stop_background()
    except Exception:
        pass
    task = getattr(app.state, "external_publisher", None)
    if task:
        task.cancel()
    try:
        from common.broker_bridge import stop_bridge
        await stop_bridge()
    except Exception:
        pass
    try:
        from watchers.runner import runner as _watcher_runner
        await _watcher_runner.stop()
    except Exception:  # noqa: BLE001 - shutdown goes on whatever the runner says
        log.debug("watcher runner stop failed", exc_info=True)
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

    # Requests per minute per principal (common/rate_limit.py, docs/api-keys.md
    # "Rate limits"). Off unless AGENTS_HUB_RATE_LIMIT_PER_MINUTE or the key's
    # own limit is set. The event stream is one long request the browser
    # reopens on its own, and open paths have no principal to count against.
    path = request.url.path
    if (principal is not None and path.startswith(("/api", "/v1"))
            and not path.startswith(("/api/stream", "/api/auth/ticket"))):
        from common import auth as _auth
        from common import rate_limit
        if not _auth.is_open_path(request.method, path):
            allowed_now, retry_after = rate_limit.check_request(principal)
            if not allowed_now:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded", "retry_after": retry_after},
                    headers={"Retry-After": str(retry_after)})

    token = identity.set_current_user(principal.id if principal else None)
    # A personal key's id, so a run this request launches (directly or
    # through a relayed chat turn) carries key_id (common/api_keys.py,
    # docs/costs.md "Attribution"), the same contextvar trick as the user id
    # just above.
    from common import api_keys as _api_keys
    _key_id = (principal.credential_id if principal is not None
              and getattr(principal, "via", "") == "api_key" else None)
    key_token = _api_keys.set_current_key_id(_key_id)
    try:
        response = await call_next(request)
    finally:
        identity.reset_current_user(token)
        _api_keys.reset_current_key_id(key_token)

    # The audit trail (common/audit.py): every write request by a person,
    # with its outcome, in token and multi mode. The key points (login, role
    # changes, launches, approvals, policy, budget) record themselves in
    # every mode from their own routes. Recorded after the response so an
    # audit failure can never turn into a failed request.
    try:
        from common import audit
        if (audit.should_log_request(request.method, request.url.path, principal)
                and audit.requests_enabled()):
            audit.record(
                f"http.{request.method.lower()}", principal=principal,
                workspace=_workspace_of(request), ip=identity.client_ip(request),
                method=request.method, path=request.url.path,
                result=str(response.status_code),
            )
    except Exception:
        pass
    return response


def _workspace_of(request) -> Optional[str]:
    """The workspace a request names, for the audit row (same rule as the guard)."""
    from common.auth import workspace_from_request
    return workspace_from_request(
        path=request.url.path,
        query_workspace=request.query_params.get("workspace"),
        header_workspace=request.headers.get("x-workspace"),
    )


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


# A run launched with a personal key that already spent its monthly budget
# (common.api_keys.KeyBudgetExceededError, docs/api-keys.md "Money quota"):
# refused with the same 429 shape as every other rate limit, not a 500.
from common.api_keys import KeyBudgetExceededError as _KeyBudgetExceededError


@app.exception_handler(_KeyBudgetExceededError)
async def _key_budget_exceeded(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=429, content={"detail": str(exc)})

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

# The chat widget's edge (widgets/edge.py, docs/widget.md): serves /widget.js
# and answers CORS for /api/widgets/public/* per widget, from that widget's
# allowed origins and without credentials. Registered after CORSMiddleware so
# it is the outermost layer: it answers a widget preflight before the global
# CORS setup would refuse an origin it does not know, and replaces that
# setup's headers on the way out, so ALLOW_ORIGINS stays exactly as narrow
# as it is for the dashboard.
from widgets.edge import WidgetEdgeMiddleware
app.add_middleware(WidgetEdgeMiddleware)

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

# Local models (feature 5): an external Ollama managed from the UI and the
# hub's own runtime under deploy/models. See docs/local-models.md.
from routes import local_models as local_models_router
app.include_router(local_models_router.router)

# The hub as a provider (feature 5C): OpenAI-compatible /v1 served by the hub
# itself, authorised by personal API keys. See docs/hub-as-provider.md.
from routes import openai_compat as openai_compat_router
app.include_router(openai_compat_router.router)
app.include_router(openai_compat_router.serving_router)

# Model structure (feature 6): GGUF and safetensors headers as one block graph.
from routes import model_structure as model_structure_router
app.include_router(model_structure_router.router)

# Preview (feature 7a): project and container pages through the hub, behind a
# short-lived ticket. See docs/containers.md.
from routes import preview as preview_router
app.include_router(preview_router.router)
app.include_router(preview_router.public_router)

# Project deployments: a project's frontend and backend run from inside the
# hub, previewed through the ticket proxy and published under /apps/<slug>/.
# See docs/project-deployments.md.
from routes import project_deployments as project_deployments_router
app.include_router(project_deployments_router.router)
app.include_router(project_deployments_router.list_router)
app.include_router(project_deployments_router.apps_router)

# Browser (feature 7b): the agent's browser session on screen, and free
# browsing on the same service. See docs/browser.md.
from routes import browser as browser_router
app.include_router(browser_router.router)

# Terminal: a shell in a run's or a service replica's container, over a
# ticketed WebSocket. See docs/terminal.md.
from routes import terminal as terminal_router
app.include_router(terminal_router.router)

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
# /api/runs/{id}/agent-version and rollback-agent: the agent version a run ran
# and a rollback to it (routes/messages.py).
app.include_router(messages.runs_router)

# Live agent copies: what is running right now, and how to write to one.
app.include_router(instances.router)
# Services: agents kept running as replicas, and the runner every chat turn
# goes to (docs/services.md).
from routes import services as services_router  # noqa: E402
app.include_router(services_router.router)

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

# Environments: execution profiles for runs, instances and scheduled jobs
from routes import environments as environments_router
app.include_router(environments_router.router)

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
    agent_proactive as agent_proactive_router,
)
for _loop_router in (tool_policy_router, outcomes_router, steering_router, guardrails_router,
                     agent_loop_settings_router, memory_versions_router, memory_consolidation_router,
                     agent_proactive_router):
    app.include_router(_loop_router.router)

# A tool call waiting for a person inside a chat turn: the chat's Approve and
# Deny, and the waiting run's side under /api/run-state (docs/hooks.md).
from routes import tool_approvals as tool_approvals_router
app.include_router(tool_approvals_router.router)

# An agent's own domain lists for web_search, fetch_url and the browser
# (tools/web.py), on top of the workspace's and the global ones.
from routes import agent_web_domains as agent_web_domains_router
app.include_router(agent_web_domains_router.router)

# Watchers (watchers/, docs/watchers.md): observers that wake a proactive agent.
from routes import watchers as watchers_router
app.include_router(watchers_router.router)

# Fourth-cycle stage 3: files a workspace keeps by id (chat, memory, tasks and
# evals reuse them), and the chat widget an outside site embeds with one tag.
from routes import files as files_router, widget as widget_router
app.include_router(files_router.router)
app.include_router(widget_router.router)

# Fourth-cycle stage 4: the spend report by key, user and project, the registry
# of agents and MCP servers with owners and approval, and the support bundle
# with the SLO status.
from routes import (accounting as accounting_router, registry as registry_router,
                    support as support_router)
app.include_router(accounting_router.router)
app.include_router(registry_router.router)
app.include_router(support_router.router)

# Docs domain: a corpus document (the changelog) for the in-app Docs page.
from routes import docs as docs_router
app.include_router(docs_router.router)

# External domain: the public address of published instances (token auth)
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
app.include_router(channels_router.router)
app.include_router(connectors_router.router)
app.include_router(slack_router.router)
app.include_router(teams_channel_router.router)
app.include_router(trackers_router.router)
app.include_router(google_router.router)
# Consent portal: a widget or channel end user grants their own Google or
# Microsoft account; the public page lives outside /api. See docs/consent.md.
from routes import consent as consent_router
app.include_router(consent_router.router)
app.include_router(consent_router.public_router)
app.include_router(databases_router.router)

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

# Cluster domain: the map of members, leases, queue and where everything runs
# (docs/deployment.md, "The cluster map"), under /api/cluster; /api/deployment
# stays live as an alias for anything still calling the old path.
app.include_router(deployment_router.router)
app.include_router(deployment_router.deployment_alias_router)

# System workspace domain: the repository copy, the maintenance loop and its
# branches (docs/system-workspace.md).
app.include_router(system_router.router)

# Demo workspace domain: present or not, add or remove (docs/demo.md).
app.include_router(demo_router.router)

# Views domain: rich agent-generated views + their assets and per-user state
app.include_router(views.router)

# Notifications domain: outbound webhook/slack endpoints, alert rules, and the
# inbound task-filing webhook.
app.include_router(notify_router.router)

# Identity domain: the auth-mode probe, login/logout, users and workspace
# membership. Registered in every mode — ``GET /api/auth/mode`` is how the
# frontend learns there is nothing to render.
app.include_router(auth_router.router)

# Stage 3 of the identity plan (docs/identity.md): single sign-on, groups
# and their mappings, SCIM provisioning, the audit trail, the caller's own
# account (sessions, API keys) and workspace secrets. Each router answers
# 404 outside the mode it needs, the same way the accounts routes do.
app.include_router(oidc_router.router)
app.include_router(groups_router.router)
app.include_router(scim_router.router)
app.include_router(audit_router.router)
app.include_router(account_router.router)
app.include_router(secrets_router.router)
# The GitHub App: installations bound to workspaces, and people connecting
# their own GitHub account (connectors/git/github_app.py).
app.include_router(github_app_router.router)

# An agent's default outcome rubric (fifth-cycle stage 4 "kits", tasks/outcome.py).
from routes import agent_outcome as agent_outcome_router
app.include_router(agent_outcome_router.router)

# Industry agent kits: ready-made bundles a workspace installs in one step
# (docs/kits.md, the ``kits/`` package, ``declarative/`` underneath).
from routes import kits as kits_router
app.include_router(kits_router.router)

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

    options = uvicorn_options()
    # The reloader needs an import string. Without it, hand over the app this
    # run has already built: "main:app" would execute this file a second time.
    uvicorn.run("main:app" if options["reload"] else app, **options)
