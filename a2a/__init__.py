"""A2A (Agent2Agent) support: this hub as an A2A server and as an A2A client.

Three modules, split by direction and kept free of transport code so both ends
of the protocol can be tested without a network:

- :mod:`a2a.card`   the Agent Card, built for our agents and validated for theirs.
- :mod:`a2a.server` JSON-RPC envelopes and the hub-to-A2A mapping, for the
  router in ``dashboard/backend/routes/a2a.py``.
- :mod:`a2a.client` the request/response wire format, for
  ``agents.remote_agent.RemoteAgent`` when it talks to an imported A2A agent.

See docs/a2a.md for what is implemented and what is not.
"""
from a2a import card, client, server  # noqa: F401

__all__ = ["card", "client", "server"]
