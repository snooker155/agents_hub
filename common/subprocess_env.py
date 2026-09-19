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


def base_subprocess_env(workspace_name: str) -> Dict[str, str]:
    """Base environment every agent-running subprocess needs.

    A copy of the current environment plus the workspace name and the OpenAI key
    when configured. State lives in the shared SQLite database, which the
    subprocess opens for itself via ``common.paths``, so no store path is
    injected. Callers layer their own run metadata on top.
    """
    from common.config import settings
    env = os.environ.copy()
    env["AGENT_WORKSPACE"] = workspace_name
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key
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
