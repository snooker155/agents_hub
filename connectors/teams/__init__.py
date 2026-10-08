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

_STORE = ChannelStore("teams", secret_fields=("app_password",),
                      defaults={"distribution": "private"})

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
        # The Teams app package and the store or organisation catalog
        # (docs/distribution.md).
        ConfigField("distribution", kind="select", options=["private", "public"],
                    placeholder="private"),
        ConfigField("app_name", kind="text", placeholder="Agents Hub"),
        ConfigField("developer_name", kind="text", placeholder="who publishes the app"),
        ConfigField("website_url", kind="text", placeholder="https://... (the hub by default)"),
        ConfigField("privacy_url", kind="text", placeholder="https://.../privacy"),
        ConfigField("terms_url", kind="text", placeholder="https://.../terms"),
        ConfigField("app_version", kind="text", placeholder="1.0.0"),
    ],
    inbound_url="/api/channels/teams/messages",
    has_loop=False,
)

__all__ = ["SPEC"]
