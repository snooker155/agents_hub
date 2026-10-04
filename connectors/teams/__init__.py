"""
Teams channel registration (see ``connectors/channels/registry.py``).

Bot Framework / Azure Bot: Teams pushes every activity to
``dashboard/backend/routes/teams_channel.py`` (``inbound_url``), so the channel has
no background loop (``has_loop=False``) — ``connectors/teams/service.py``
only replies and verifies credentials. ``connectors/teams/auth.py`` holds
both halves of the Bot Framework handshake: the hub's own outbound token and
the verification of Teams' inbound bearer token.
"""
from __future__ import annotations

from connectors.channels.registry import ChannelSpec, ConfigField
from connectors.channels.store import ChannelStore

from .service import TeamsService

_STORE = ChannelStore("teams", secret_fields=("app_password",))

SPEC = ChannelSpec(
    name="teams",
    store=_STORE,
    service=TeamsService(_STORE),
    fields=[
        ConfigField("app_id", kind="text",
                    placeholder="Microsoft App ID of the bot registration", required=True),
        ConfigField("app_password", secret=True, kind="password",
                    placeholder="the bot registration's client secret", required=True),
        ConfigField("tenant_id", kind="text", placeholder="single-tenant bots only"),
    ],
    inbound_url="/api/channels/teams/messages",
    has_loop=False,
)

__all__ = ["SPEC"]
