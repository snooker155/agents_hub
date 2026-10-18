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
from typing import Dict, Optional, Iterable


#: Hub credentials an older install may still carry in its environment.
HUB_CREDENTIAL_ENV = ("AGENTS_HUB_API_TOKEN", "AGENTS_HUB_SERVICE_TOKEN", "AGENTS_HUB_API_KEY",
                      "AGENTS_HUB_RUN_TOKEN")


def strip_hub_credentials(env: Dict[str, str]) -> Dict[str, str]:
    """Drop every hub credential from a child's ``env``; returns it."""
    for name in HUB_CREDENTIAL_ENV:
        env.pop(name, None)
    return env


def base_subprocess_env(
    workspace_name: str,
    *,
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    key_id: Optional[str] = None,
    extra_secret_names: Optional[Iterable[str]] = None,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
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
    run inherits the same dict through ``container_env``.

    """
    from common.config import settings
    env = os.environ.copy()
    env["AGENT_WORKSPACE"] = workspace_name
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key
    # A provider key saved since the backend started (common/provider_env.py).
    from common import provider_env
    provider_env.overlay(env)
    # No hub credential belongs in a process an agent's shell runs in: the API
    # asks for none, and whatever an older .env still carries is dropped.
    strip_hub_credentials(env)
    # Who the run is charged to, so the runs the child creates in turn are
    # charged to the same user (common/attribution.py).
    from common.attribution import child_env
    env.update(child_env(user_id))
    # Last, so a declared secret wins over the same name inherited from the
    # host environment: an agent-scoped GITHUB_TOKEN is the whole point of
    # giving an agent its own identity.
    if agent_id or flow_id:
        from common import secrets as _secrets
        if agent_id:
            env.update(_secrets.env_for_run(workspace_name, agent_id, user_id,
                                            extra_names=extra_secret_names))
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
