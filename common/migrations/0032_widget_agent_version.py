"""
0032: a widget may pin the agent version its visitors talk to
(widgets/service.py, docs/widget.md "Agent version").

A widget on a public site is a deployment of one agent, and like a service
(0030, ``services.agent_version``) it should answer with the version that was
tried, not whatever the agent's live definition happens to be today. The
``widgets`` row gains ``agent_version`` (NULL means the live definition);
each visitor turn is built from it (widgets/turn.py, ChatRequest.agent_version).
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "widgets", "agent_version", "INTEGER")
