"""
0056: a person's own rate limits (docs/api-keys.md "Rate limits").

Adds ``users.rate_limit_per_minute`` and ``users.tokens_per_day``, both
nullable. NULL, what every existing account gets, means the person follows
the hub-wide setting (``AGENTS_HUB_RATE_LIMIT_PER_MINUTE``,
``AGENTS_HUB_RATE_LIMIT_TOKENS_PER_DAY``); a number overrides it for that
person alone, 0 meaning unlimited. A personal key's own values (migration
0017) still win over the person's. ``common/rate_limit.py`` reads them.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "users", "rate_limit_per_minute", "INTEGER")
    add_column_if_missing(conn, dialect, "users", "tokens_per_day", "INTEGER")
