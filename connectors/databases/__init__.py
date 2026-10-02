"""
Read-only databases: Postgres, MySQL, ClickHouse, SQLite, registered as a
credential connector (connectors/credentials.py) with no single shared
secret. Unlike Jira or Linear (one base URL/token), an operator attaches
several named connections at once, each carrying its own write-only dsn, so
the real CRUD, test, schema and query API lives on its own router
(dashboard/backend/routes/databases.py) rather than on the generic
``/api/connectors/databases/config`` route. That generic route still lists
databases on the Connectors page (it reports ``configured: true`` always,
since there is nothing to configure at the connector level) and surfaces the
supported kinds through ``extra``.

store.py holds the list of named connections; drivers.py is the one
function-per-kind implementation that actually opens a connection, behind
guard.py's read-only statement check. tools/databases.py is the agent-facing
read path (db_list_connections, db_schema, db_query).
"""
from __future__ import annotations

from typing import Any, Dict

from connectors.credentials import CredentialSpec

from .store import KINDS, STORE


def _extra() -> Dict[str, Any]:
    return {"kinds": list(KINDS)}


CREDENTIALS = CredentialSpec(
    name="databases",
    store=STORE,
    fields=[],
    test=None,
    extra=_extra,
    required=(),
)

__all__ = ["CREDENTIALS"]
