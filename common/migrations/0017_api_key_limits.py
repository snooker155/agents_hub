"""
0017: a personal API key's own rate limits (docs/api-keys.md "Rate limits").

Adds ``api_keys.rate_limit_per_minute`` and ``api_keys.tokens_per_day``, both
nullable. NULL, what every existing key gets, means the key follows the
hub-wide ``AGENTS_HUB_RATE_LIMIT_PER_MINUTE`` and
``AGENTS_HUB_RATE_LIMIT_TOKENS_PER_DAY``; a number overrides them for that
key alone, 0 meaning unlimited. ``common/rate_limit.py`` reads them through
``common.api_keys.get_key``.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "api_keys", "rate_limit_per_minute", "INTEGER")
    add_column_if_missing(conn, dialect, "api_keys", "tokens_per_day", "INTEGER")
