"""
0051: a person's first and last name and their language (docs/identity.md,
"Accounts"; docs/installation.md, "The welcome for a new account").

Adds ``users.first_name``, ``users.last_name`` and ``users.language``, all
nullable. An administrator sets them when making the account or later in its
edit window; with a name the display name is "First Last". The language is
the one the account's welcome opens in, and what the person picks there is
saved back to it. NULL, what every existing account gets, reads as English
(``common/identity.py`` ``DEFAULT_LANGUAGE``).
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "users", "first_name", "TEXT")
    add_column_if_missing(conn, dialect, "users", "last_name", "TEXT")
    add_column_if_missing(conn, dialect, "users", "language", "TEXT")
