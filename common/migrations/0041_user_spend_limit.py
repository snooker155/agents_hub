"""
0041: a person's own spend limit (docs/costs.md "Limit per person").

Adds ``users.spend_limit_usd``, nullable. NULL, what every existing account
gets, means the person follows the hub-wide default
(``AGENTS_HUB_USER_SPEND_LIMIT_USD``); a number overrides it for that person
alone, 0 meaning unlimited. ``common/user_budget.py`` reads it.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "users", "spend_limit_usd", "REAL")
