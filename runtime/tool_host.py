"""
Connector tools of an agent container, run by the process that launched it.

A connector tool (a tracker, Google Workspace, Microsoft Graph, Notion,
Confluence, a database, a chat channel) needs the connector's secrets: OAuth
tokens it refreshes, a database password it connects with. Those never reach
an agent container. In the container the tool is a stand-in
(:func:`container_tools`) that sends the call to its tool host, the worker that
started the container (or the backend, where no worker runs:
``AGENTS_HUB_TOOL_HOST_URL``), with the container's run token. The host runs
the very same tool object in the token's workspace, as the agent the call
names, and sends the result back. The API backend carries none of this load in
a deployment with workers.

Served at ``POST /api/run-state/tool-host/<tool>`` by the backend (in the main
app) and by every worker (its own small server, :func:`serve_in_thread`).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

# At module level: the route's annotations are strings (``from __future__``),
# and FastAPI resolves them in this module's globals.
from fastapi import Request

log = logging.getLogger(__name__)

#: Where a container's connector tools run (set by the launcher).
URL_ENV = "AGENTS_HUB_TOOL_HOST_URL"

#: The port a worker serves its tool host on.
PORT_ENV = "AGENTS_HUB_WORKER_TOOL_PORT"
DEFAULT_PORT = 8090

#: The tool modules whose tools need a connector's secrets.
HOSTED_MODULES = (
    "tools.channel_send", "tools.trackers", "tools.google_workspace", "tools.microsoft_graph",
    "tools.knowledge", "tools.databases",
)

PATH = "/api/run-state/tool-host"


def hosted_tools() -> Dict[str, Any]:
    """``{name: tool}`` of every connector tool a host runs for a container."""
    import importlib
    out: Dict[str, Any] = {}
    for name in HOSTED_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - not shipped or broken: its tools are not hosted
            log.debug("tool host: %s unavailable", name, exc_info=True)
            continue
        for tool in getattr(mod, "CONNECTOR_TOOLS", []) or []:
            out[str(getattr(tool, "name", ""))] = tool
    return out


# ── the container's side ─────────────────────────────────────────────────────

def container_tools(tools: List[Any]) -> List[Any]:
    """``tools`` with every hosted connector tool replaced by a stand-in that
    runs it on the tool host. Unchanged outside an agent container."""
    from common import remote_state
    if not remote_state.is_remote() or not os.environ.get(URL_ENV):
        return tools
    hosted = set(hosted_tools())
    return [_stand_in(t) if getattr(t, "name", "") in hosted else t for t in tools]


def _stand_in(tool: Any) -> Any:
    from langchain_core.tools import StructuredTool
    name = str(tool.name)

    def run(**kwargs: Any) -> Any:
        return call(name, kwargs)

    return StructuredTool.from_function(func=run, name=name, description=tool.description,
                                        args_schema=tool.args_schema)


def call(name: str, args: Dict[str, Any]) -> Any:
    """Run one hosted tool on the tool host; its result, or its error as text
    (the way a tool's own failure reaches the model)."""
    from common import remote_state
    from common.agent_context import current_agent_id, current_run_id
    from common.attribution import launching_user
    from common.auth import auth_headers
    from common.secrets import current_end_user
    payload = {
        "args": remote_state.encode(args),
        "agent_id": current_agent_id.get() or "",
        "run_id": current_run_id.get() or os.environ.get("AGENT_RUN_ID", ""),
        "user_id": launching_user(),
        "end_user": current_end_user() or "",
    }
    url = os.environ[URL_ENV].rstrip("/") + f"{PATH}/{name}"
    try:
        resp = remote_state._http_session().post(url, json=payload, headers=auth_headers(),
                                                 timeout=remote_state._timeout())
    except Exception as exc:  # noqa: BLE001 - the tool's failure, told to the model
        return f"ERROR: {name} could not reach its tool host: {exc}"
    if resp.status_code == 401:
        raise remote_state.TokenRefused(f"{name}: the tool host no longer accepts this token")
    try:
        body = resp.json()
    except ValueError:
        return f"ERROR: {name}: the tool host answered {resp.status_code}"
    if resp.status_code >= 400:
        return f"ERROR: {name}: {body.get('detail') or resp.status_code}"
    return remote_state.decode(body.get("result"))


# ── the host's side ──────────────────────────────────────────────────────────

def run_for_token(row: Dict[str, Any], name: str, payload: Dict[str, Any]) -> Any:
    """Run tool ``name`` for the container ``row`` (a run token's row) speaks
    for: in the token's workspace, as the agent named if that agent is one the
    workspace may use and holds the tool. Raises LookupError for an unknown
    tool, PermissionError for one the agent does not hold."""
    from common import remote_state
    from common.agent_context import current_agent_id, current_run_id
    from common.workspace_context import _workspace_ctx
    tool = hosted_tools().get(name)
    if tool is None:
        raise LookupError(f"no hosted tool {name!r}")
    workspace = row.get("workspace") or "default"
    agent_id = _caller_agent(row, str(payload.get("agent_id") or ""))
    _check_holds(agent_id, name, workspace)
    args = remote_state.decode(payload.get("args") or {})
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    tokens = [(_workspace_ctx, _workspace_ctx.set(workspace)),
              (current_agent_id, current_agent_id.set(agent_id or None)),
              (current_run_id, current_run_id.set(str(row.get("run_id") or payload.get("run_id") or "")
                                                  or None))]
    from common import identity, secrets
    user_token = identity.set_current_user(str(payload.get("user_id"))) if payload.get("user_id") else None
    end_token = secrets.set_end_user(str(payload.get("end_user") or "")) if payload.get("end_user") else None
    try:
        return remote_state.encode(tool.invoke(args))
    finally:
        if end_token is not None:
            secrets.reset_end_user(end_token)
        if user_token is not None:
            identity.reset_current_user(user_token)
        for var, token in reversed(tokens):
            var.reset(token)


def _caller_agent(row: Dict[str, Any], claimed: str) -> str:
    """The agent a call runs as: the run's own for a run token; the one the
    call names for a carrier's (a runner answers for many agents)."""
    if row.get("run_id"):
        from managers.run_manager import get_run_by_id
        agent = (get_run_by_id(str(row["run_id"])) or {}).get("agent_id")
        if agent:
            return str(agent)
    if row.get("instance_id"):
        from instances import store
        agent = (store.get(str(row["instance_id"])) or {}).get("agent_id")
        if agent:
            return str(agent)
    return claimed


def _check_holds(agent_id: str, tool_name: str, workspace: str) -> None:
    from agents.registry import get_agent
    spec = get_agent(agent_id) if agent_id else None
    if spec is None:
        raise PermissionError(f"no agent {agent_id!r} to run {tool_name} as")
    if tool_name not in set(getattr(spec, "tools", None) or []):
        raise PermissionError(f"agent {agent_id} does not hold {tool_name}")
    try:
        from common.workspace_context import filter_agents_for_workspace
        if not filter_agents_for_workspace([spec], workspace):
            raise PermissionError(f"agent {agent_id} is not available in workspace {workspace}")
    except ImportError:
        pass


def router() -> Any:
    """The tool host's route, for the backend's app and a worker's server."""
    import asyncio
    from fastapi import APIRouter, HTTPException

    r = APIRouter()

    @r.post(PATH + "/{name}", include_in_schema=False)
    async def tool_host_route(name: str, request: Request):
        from common import identity, run_tokens
        row = await asyncio.to_thread(run_tokens.resolve, identity.presented_credential(request))
        if row is None:
            raise HTTPException(status_code=401, detail="a tool call needs a run token")
        payload = await request.json()
        try:
            result = await asyncio.to_thread(run_for_token, row, name, payload or {})
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"result": result}

    return r


# ── a worker's server ────────────────────────────────────────────────────────

def worker_port() -> int:
    try:
        return int(os.environ.get(PORT_ENV, "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def serve_in_thread(port: Optional[int] = None) -> Optional[threading.Thread]:
    """Serve the tool host from this worker. Never raises: a worker without
    it still launches; its containers' connector tools then answer an error."""
    try:
        import uvicorn
        from fastapi import FastAPI
        app = FastAPI(title="Agents Hub worker tool host")
        app.include_router(router())
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port or worker_port(),
                                               log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, name="tool-host", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("the worker's tool host did not start", exc_info=True)
        return None


def url_for_containers() -> str:
    """Where a container this process starts sends its connector tools: this
    worker (by its alias on the agents-hub network, or the host gateway for a
    worker on the host), else the hub's own address for containers."""
    from common.config import hub_role
    from managers import container_manager as cm
    if hub_role() != "worker":
        return cm.container_hub_url()
    if cm._host_project_root():
        return f"http://{cm.worker_network_alias()}:{worker_port()}"
    return f"http://host.docker.internal:{worker_port()}"


__all__ = ["DEFAULT_PORT", "HOSTED_MODULES", "PATH", "PORT_ENV", "URL_ENV", "call",
           "container_tools", "hosted_tools", "router", "run_for_token", "serve_in_thread",
           "url_for_containers"]
