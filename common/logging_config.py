"""Central logging configuration.

Until this module existed nothing in the hub configured the root logger: the
level saved on the Settings page (``ORCH_LOG_LEVEL``, plus its per-workspace
override) was written to ``.env`` / the workspace's metadata and then read by no
one.
``configure_logging`` is its consumer — the backend calls it at startup and every
agent subprocess calls it before it builds an agent, so the level a user picks is
the level the process actually logs at.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def _clean(value: object) -> Optional[str]:
    level = str(value or "").strip().upper()
    return level if level in LEVELS else None


def resolve_log_level(workspace: Optional[str] = None) -> str:
    """The level this process should log at.

    A workspace override wins over the process environment, which wins over the
    global default in ``common.config``. That is the same precedence the Settings
    page shows with its source badges.
    """
    if workspace:
        try:
            from workspace.storage import get_effective_settings
            level = _clean((get_effective_settings(workspace) or {}).get("orch_log_level"))
            if level:
                return level
        except Exception:
            pass  # a missing or unreadable workspace just falls through
    level = _clean(os.environ.get("ORCH_LOG_LEVEL"))
    if level:
        return level
    try:
        from common.config import settings
        return _clean(settings.orch_log_level) or "INFO"
    except Exception:
        # Settings validates ORCH_LOG_LEVEL against a Literal, so a typo in .env
        # raises here. Logging must never be the thing that stops a process from
        # starting, so a bad value just means the default.
        return "INFO"


def configure_logging_for_active_workspace() -> str:
    """Apply the log level of the workspace the UI currently has selected.

    The Settings page writes ``orch_log_level`` into that workspace's entry in
    ``workspaces.json``, not into ``.env``. Resolving without a workspace only
    ever sees the global value, which is why a level picked in the UI used to do
    nothing to the backend even across a restart.
    """
    try:
        from common.workspace_context import resolve_active_workspace
        workspace = resolve_active_workspace()
    except Exception:
        workspace = None
    # A stored selection of "default" is normalised to None elsewhere, but it is
    # a real workspace folder with its own overrides — fall back to it by name
    # rather than skipping straight to the global value.
    return configure_logging(workspace or "default")


def configure_logging(workspace: Optional[str] = None) -> str:
    """Apply the resolved level to the root logger. Returns the level applied.

    Handlers already installed by the host (uvicorn, pytest) are left alone —
    only the level is forced — so this is safe to call from inside a server
    process as well as from a bare subprocess.
    """
    level = resolve_log_level(workspace)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=getattr(logging, level), format=_FORMAT)
    root.setLevel(getattr(logging, level))
    return level
