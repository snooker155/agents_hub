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
import sys
from typing import Optional

log = logging.getLogger(__name__)

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
        except Exception:  # noqa: BLE001 - a missing or unreadable workspace just falls through
            log.debug("could not read workspace log level override for %s", workspace, exc_info=True)
    level = _clean(os.environ.get("ORCH_LOG_LEVEL"))
    if level:
        return level
    try:
        from common.config import settings
        return _clean(settings.orch_log_level) or "INFO"
    except Exception:  # noqa: BLE001 - logging must never be what stops a process from starting
        # Settings validates ORCH_LOG_LEVEL against a Literal, so a typo in .env
        # raises here. Logging must never be the thing that stops a process from
        # starting, so a bad value just means the default.
        log.debug("could not read the global log level setting", exc_info=True)
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
    except Exception:  # noqa: BLE001 - falls back to the global level, must not break startup
        log.debug("could not resolve the active workspace for logging", exc_info=True)
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


class _DynamicStreamHandler(logging.StreamHandler):
    """A ``StreamHandler`` that resolves its stream by name on every write
    instead of once at construction time.

    ``runtime/agent_run.py`` (``_setup_cli_log``, ``_setup_docker_log_tee``)
    reassigns ``sys.stdout`` to a tee after this module's loggers are built, the
    same way a plain ``print()`` call keeps working after that reassignment
    because it looks ``sys.stdout`` up at call time rather than at import time.
    A handler holding a reference to the stream object present when it was
    constructed would miss that swap and stop reaching the tee; reading the
    attribute off ``sys`` on every emit keeps it working the same way.
    """

    def __init__(self, stream_name: str = "stdout") -> None:
        super().__init__(stream=getattr(sys, stream_name))
        self._stream_name = stream_name

    @property
    def stream(self):  # type: ignore[override]
        return getattr(sys, self._stream_name)

    @stream.setter
    def stream(self, value):  # noqa: ARG002 - logging.Handler.__init__ sets this once
        # The property above is what every read (including the write in emit)
        # actually uses, so the one-time assignment from __init__ is accepted
        # and otherwise ignored.
        pass


def marker_logger(name: str) -> logging.Logger:
    """A logger for the machine-readable marker lines a runtime entrypoint's
    stdout carries: ``[flow_start]``, ``[heartbeat]``, ``Agent output:`` and
    the like.

    ``agents/agent_launcher.py`` pipes a run's stdout straight into that run's
    log file, and both the dashboard (``dashboard/backend/routes/sessions.py``)
    and several tests grep the result for these exact strings, so the handler
    this builds:

    * writes only the message, with no timestamp or level prefix, so the text
      a consumer greps for is byte-identical to what the ``print()`` calls this
      replaces used to emit;
    * always logs at INFO regardless of ``ORCH_LOG_LEVEL``, since a ``print()``
      never respected a log level either;
    * does not propagate to the root logger, so a handler installed there by
      ``configure_logging`` (on stderr, with the timestamp/level prefix) never
      prints the same line a second time.
    """
    log = logging.getLogger(name)
    log.propagate = False
    log.setLevel(logging.INFO)
    if not any(isinstance(h, _DynamicStreamHandler) for h in log.handlers):
        handler = _DynamicStreamHandler("stdout")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    return log
