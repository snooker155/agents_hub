"""
Slack channel registration (see ``connectors/channels/registry.py``).

Exports ``SPEC``: the config fields the Connectors page renders, the store
(one document, ``connectors/channels/store.py``) and the service
(``connectors/slack/service.py``) the singleton supervisor runs. The
Events API webhook that feeds ``events`` mode lives in
``dashboard/backend/routes/slack.py``, wired in by the dashboard's
``main.py``.
"""
from __future__ import annotations

from connectors.channels.registry import ChannelSpec, ConfigField
from connectors.channels.store import ChannelStore

from .service import SlackService

_STORE = ChannelStore(
    "slack",
    secret_fields=("bot_token", "app_token", "signing_secret", "client_secret"),
    defaults={"mode": "socket", "distribution": "private"},
)

SPEC = ChannelSpec(
    name="slack",
    store=_STORE,
    service=SlackService(_STORE),
    fields=[
        # Required for a bot of one team; a distributed app (client_id and
        # client_secret, docs/distribution.md) gets a token per installation.
        ConfigField("bot_token", secret=True, kind="password",
                    placeholder="xoxb-... (one team)", required=True,
                    optional_when={"mode": "events"}),
        ConfigField("app_token", secret=True, kind="password",
                    placeholder="xapp-... (Socket Mode)"),
        ConfigField("signing_secret", secret=True, kind="password",
                    placeholder="needed for the Events API webhook"),
        ConfigField("mode", kind="select", options=["socket", "events"],
                    placeholder="socket"),
        # Distribution through OAuth and the Slack Marketplace (events mode only).
        ConfigField("client_id", kind="text", placeholder="OAuth client ID, for installs",
                    hidden_when={"mode": "socket"}),
        ConfigField("client_secret", secret=True, kind="password",
                    placeholder="OAuth client secret", hidden_when={"mode": "socket"}),
        ConfigField("distribution", kind="select", options=["private", "public"],
                    placeholder="private", hidden_when={"mode": "socket"}),
        ConfigField("app_name", kind="text", placeholder="Agents Hub",
                    hidden_when={"mode": "socket"}),
    ],
    inbound_url="/api/channels/slack/events",
    has_loop=True,
)

__all__ = ["SPEC"]
