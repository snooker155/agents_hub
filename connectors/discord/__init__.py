"""
Discord channel registration (see ``connectors/channels/registry.py``).

Exports ``SPEC``: the config fields the Connectors page renders, the store
(one document, ``connectors/channels/store.py``) and the service
(``connectors/discord/service.py``) the singleton supervisor runs. Discord
has no inbound webhook, only the Gateway, so unlike Slack there is no
``dashboard/backend/routes/discord.py``.
"""
from __future__ import annotations

from connectors.channels.registry import ChannelSpec, ConfigField
from connectors.channels.store import ChannelStore

from .service import DiscordService

_STORE = ChannelStore("discord", secret_fields=("bot_token",))

SPEC = ChannelSpec(
    name="discord",
    store=_STORE,
    service=DiscordService(_STORE),
    fields=[
        ConfigField("bot_token", secret=True, kind="password",
                    placeholder="bot token from the Discord developer portal", required=True),
        ConfigField("application_id", placeholder="application id (informational)"),
    ],
    inbound_url=None,
    has_loop=True,
)

__all__ = ["SPEC"]
