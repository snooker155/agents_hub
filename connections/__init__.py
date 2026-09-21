"""Connections: code that runs elsewhere and reports into this hub.

A *connection* is the third way something external becomes visible here, and
the only one where the hub does not drive the work:

* an **agent definition** is behaviour this hub owns and runs (``agents/``);
* an **imported agent** runs in its own process and the hub calls it over HTTP
  (``agents/remote_agent.py``) — the hub still starts every run;
* a **connection** is a graph, crew or service running on someone else's
  trigger, which posts what it is doing to ``/api/ingest``. The hub starts
  nothing and only watches.

The third case is what a team with a working LangGraph flow in production
actually needs: a UI and monitoring, without handing over control of when their
agents run. See EXTERNAL_CONNECTIONS.md for the design and the two modes.

The package is deliberately thin: :mod:`connections.store` owns the record and
its token, :mod:`connections.service` turns reported frames into the same run
records, sessions and live events a local run produces. The translation of the
frames themselves is shared with the pull path in
:mod:`common.agent_frames`, so a run looks the same whichever direction it
arrived from.
"""
from connections.store import (  # noqa: F401
    Connection,
    create_connection,
    delete_connection,
    get_connection,
    list_connections,
    resolve_token,
    rotate_token,
    set_topology,
    touch,
    update_connection,
)

__all__ = [
    "Connection",
    "create_connection",
    "delete_connection",
    "get_connection",
    "list_connections",
    "resolve_token",
    "rotate_token",
    "set_topology",
    "touch",
    "update_connection",
]
