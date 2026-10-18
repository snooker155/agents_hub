"""
Tools that act through a connector, gathered into one list.

Each connector package ships its own tool module (``tools/trackers.py``),
exporting ``CONNECTOR_TOOLS``. The agent factory and the tool catalog
read this one function instead of knowing the modules, and a module that is
missing or fails to import (an optional dependency absent in this install)
is skipped and logged, so every other tool still shows.

Capability grants for every tool id listed here live in
``tools/capabilities.py`` (the "connectors" block), which is the one place a
tool's security claim is stated.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, List

log = logging.getLogger(__name__)

MODULES = (
    "tools.trackers",
    # Not a connector either: the main agent's workspace management
    # (tools/workspace_management.py), catalogued under agent_management.
    "tools.workspace_management",
)


def connector_tools() -> List[Any]:
    tools: List[Any] = []
    for mod_name in MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            if exc.name == mod_name:
                continue  # not shipped in this build
            log.warning("connector tools %s skipped: %s", mod_name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - one broken module must not hide the rest
            log.warning("connector tools %s skipped: %s", mod_name, exc)
            continue
        tools.extend(getattr(mod, "CONNECTOR_TOOLS", []) or [])
    return tools


__all__ = ["connector_tools", "MODULES"]
