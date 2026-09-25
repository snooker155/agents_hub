"""
0025: pricing on ``/v1`` and a money quota per personal API key
(fourth cycle, stage 4, stream A, docs/costs.md, docs/api-keys.md).

``serving_usage.cost_usd`` is the estimated USD cost of a served completion
(catalog pricing, ``common.pricing``), computed once at record time so the
Models page and the accounting report never re-derive it from tokens.
Existing rows read 0.0 (a served call before this migration cost nothing on
its own record; the report still shows every row, just at whatever price it
carries).

``api_keys.budget_usd_per_month`` is a key's own money cap: NULL (every
existing key) or 0 means no cap, a positive number refuses further spend on
that key once its current UTC month's cost (``/v1`` serving plus the runs it
launched) reaches it. ``common.api_keys.key_month_spend_usd`` computes the
figure; ``common.rate_limit`` and ``routes/openai_compat.py`` enforce it.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "serving_usage", "cost_usd", "REAL")
    add_column_if_missing(conn, dialect, "api_keys", "budget_usd_per_month", "REAL")
