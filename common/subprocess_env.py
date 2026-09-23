"""
Subprocess environment builders for agent/flow runs.

Every launcher that spawns an agent-running subprocess (``agents.agent_launcher``,
``flow.launcher``) needs the same base environment, and some need the same
run-metadata layer on top. These composable builders keep that wiring in one
place instead of duplicated per launcher.

Compose from the base outward::

    env = base_subprocess_env(ws_name)
    add_run_env(env, session_id, log_file)            # session + log
"""
from __future__ import annotations

import os
from typing import Dict, Optional


def base_subprocess_env(
    workspace_name: str,
    *,
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> Dict[str, str]:
    """Base environment every agent-running subprocess needs.

    A copy of the current environment plus the workspace name and the OpenAI key
    when configured. State lives in the shared SQLite database, which the
    subprocess opens for itself via ``common.paths``, so no store path is
    injected. Callers layer their own run metadata on top.

    With ``agent_id`` (or ``flow_id``), the workspace secrets that agent
    declares in ``AgentSpec.secrets`` are merged in, resolved for ``user_id``
    (the user who launched the run) by ``common.secrets``: only the declared
    names, most specific scope first, nothing at all on any error. A docker
    run inherits the same dict through ``container_env``. Called with the
    workspace alone, the result is exactly what it always was.
    """
    from common.config import settings
    env = os.environ.copy()
    env["AGENT_WORKSPACE"] = workspace_name
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key
    # The token that gates /api requests (see common/auth.py). A plain
    # os.environ.copy() already carries it when it was exported as a real
    # environment variable, but pydantic-settings also accepts it from .env
    # without ever writing it back to os.environ — so a token configured only
    # that way would silently fail to reach the subprocess, and every relay
    # POST it makes (SessionPublishCallback, session_broker._relay_notify)
    # would get a 401. Set it explicitly, the same way as OPENAI_API_KEY above.
    if settings.api_token:
        env["AGENTS_HUB_API_TOKEN"] = settings.api_token
    # AUTH_MODE=multi with no shared token: a subprocess has no session and no
    # password, so it cannot authenticate as a user at all. The backend mints
    # one random service credential per process and hands it down here; it acts
    # as admin for the relay POSTs and dies with the process that issued it.
    # See common/identity.py, "the service credential".
    from common.identity import SERVICE_TOKEN_ENV, current_mode, service_token
    if current_mode() == "multi" and not settings.api_token:
        env[SERVICE_TOKEN_ENV] = service_token()
    # Last, so a declared secret wins over the same name inherited from the
    # host environment: an agent-scoped GITHUB_TOKEN is the whole point of
    # giving an agent its own identity.
    if agent_id or flow_id:
        from common import secrets as _secrets
        if agent_id:
            env.update(_secrets.env_for_run(workspace_name, agent_id, user_id))
        else:
            env.update(_secrets.env_for_flow(workspace_name, flow_id, user_id))
    return env


def add_run_env(
    env: Dict[str, str],
    session_id: Optional[str] = None,
    log_file: Optional[str] = None,
) -> Dict[str, str]:
    """Add per-run metadata (session id, log file) that run_agent reads back.

    Mutates and returns ``env``. Only sets keys whose value is provided."""
    if session_id:
        env["AGENT_SESSION_ID"] = session_id
    if log_file:
        env["AGENT_LOG_FILE"] = str(log_file)
    return env
