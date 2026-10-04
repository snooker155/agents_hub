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

    ``run_id`` and ``session_id`` name the launch the run token is minted for;
    closing that run retires the token (``common.run_tokens.retire_for_run``).
    """
    from common.config import settings
    env = os.environ.copy()
    env["AGENT_WORKSPACE"] = workspace_name
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key
    # The credential the child calls back with (its relays and, from a
    # container, its run state): a run token of its own that reaches only those
    # routes (common/run_tokens.py, common/auth.py RELAY_ROUTES). The shared API
    # token and the admin service credential used to ride here, and either one
    # reaches the whole API from a process an agent's shell runs in; both are
    # taken out of the copied environment too. Nothing is minted in single
    # mode, where the API asks for no credential.
    from common import run_tokens
    run_tokens.mint_for_env(env, kind="run", run_id=run_id, session_id=session_id,
                            workspace=workspace_name or None)
    # Who the run is charged to, so the runs the child creates in turn are
    # charged to the same user and personal key (common/attribution.py).
    from common.attribution import child_env
    env.update(child_env(user_id, key_id))
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
        route_secrets(env, workspace=workspace_name)
    return env


def route_secrets(env: Dict[str, str], *, execution_mode: Optional[str] = None,
                  workspace: Optional[str] = None) -> Dict[str, str]:
    """Send a run that holds host-bound secret placeholders through the egress
    proxy, with the hub CA in its trust store (environments/secret_egress.py).

    A no-op for a run without placeholders. Launchers call it once more after
    layering an environment's own proxy URL on top, so the secret hosts land
    on the token the run actually uses. A routing failure is logged, never
    raised: the run then holds placeholders nobody swaps, which fails its
    requests but leaks nothing.
    """
    try:
        from environments.secret_egress import route_env
        return route_env(env, execution_mode=execution_mode, workspace=workspace)
    except Exception:  # noqa: BLE001 - see docstring
        import logging
        logging.getLogger(__name__).error("secret placeholders could not be routed through the "
                                          "egress proxy", exc_info=True)
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
