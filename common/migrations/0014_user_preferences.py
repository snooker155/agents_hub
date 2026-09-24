"""
0014: personal preferences (docs/settings.md "Palette").

Adds ``users.preferences``, a JSON column for whatever a signed-in person
sets for themselves and nobody else — today just a palette (2 to 4 base
colors that src/lib/palette.js turns into shades, panels and borders),
read and written through ``GET``/``PUT /api/auth/preferences``
(``routes/account.py``, ``common.identity.get_preferences`` /
``set_preferences``). Empty for every existing user until they set
something, and stays empty outside ``AUTH_MODE=multi`` the same way the
rest of the identity tables do (``routes/account.py`` 404s the endpoint
there, so nothing ever writes to it).
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "users", "preferences", "TEXT")
