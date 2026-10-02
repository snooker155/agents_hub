"""
The mail channel: IMAP inbound, SMTP outbound.

Exports ``SPEC`` for ``connectors.channels.registry.load_builtin``. Unlike
Slack or Teams, mail has no inbound webhook (``inbound_url=None``); it has a
background loop instead (``has_loop=True``, see ``service.MailService``) that
polls the mailbox on a timer.

The package is named ``mail``, not ``email``, so an absolute ``import email``
anywhere in this package keeps meaning the stdlib module.
"""
from __future__ import annotations

from connectors.channels.registry import ChannelSpec, ConfigField
from connectors.channels.store import ChannelStore

from .service import MailService

#: Fields the UI must never echo back; ``ChannelStore.public_config`` turns
#: each into a ``has_<field>`` flag instead.
_SECRET_FIELDS = ("imap_password", "smtp_password")

#: Non-secret defaults a fresh config starts with.
_DEFAULTS = {
    "imap_port": 993,
    "imap_folder": "INBOX",
    "imap_ssl": "yes",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "poll_seconds": 60,
    "subject_prefix": "Re:",
}

store = ChannelStore("mail", secret_fields=_SECRET_FIELDS, defaults=_DEFAULTS)
# Not named ``service`` at module scope: that would shadow the submodule
# ``connectors/mail/service.py`` on this package object, so
# ``connectors.mail.service`` (the module tests and callers import) would
# resolve to this instance instead.
mail_service = MailService(store)

SPEC = ChannelSpec(
    name="mail",
    store=store,
    service=mail_service,
    fields=[
        ConfigField("imap_host", required=True, placeholder="imap.example.com"),
        ConfigField("imap_port", kind="number", placeholder="993"),
        ConfigField("imap_user", required=True, placeholder="bot@example.com"),
        ConfigField("imap_password", secret=True, kind="password", required=True),
        ConfigField("imap_folder", placeholder="INBOX"),
        ConfigField("imap_ssl", kind="select", options=["yes", "no"]),
        ConfigField("smtp_host", required=True, placeholder="smtp.example.com"),
        ConfigField("smtp_port", kind="number", placeholder="587"),
        ConfigField("smtp_user", placeholder="bot@example.com"),
        ConfigField("smtp_password", secret=True, kind="password"),
        ConfigField("smtp_security", kind="select", options=["starttls", "ssl", "none"]),
        ConfigField("from_address", required=True, placeholder="bot@example.com"),
        ConfigField("poll_seconds", kind="number", placeholder="60"),
        ConfigField("subject_prefix", placeholder="Re:"),
    ],
    inbound_url=None,
    has_loop=True,
)

__all__ = ["SPEC", "store", "mail_service"]
