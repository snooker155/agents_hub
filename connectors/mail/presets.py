"""
Server settings of the popular mailbox providers, so a person picks "Gmail"
instead of looking up ``imap.gmail.com`` and its port.

One table serves two forms: the IMAP watcher (watchers/kinds.py, the
``imap`` kind) and the mail channel (connectors/mail, IMAP in and SMTP out).
Each preset carries the IMAP endpoint, the SMTP endpoint, and ``auth``: how
the provider wants the password typed.

``auth``
    ``app_password``: the account password does not work for IMAP; the
    person creates an app password in the account's security settings
    (``help_url`` points there), usually after turning on two-step
    verification.
    ``password``: the ordinary account password works, sometimes after IMAP
    access is switched on in the mailbox settings.
    ``oauth``: the provider has retired password sign-in for IMAP and SMTP.
    The preset still fills the hosts, for a tenant that keeps basic auth on,
    but the form warns that a password is unlikely to be accepted.

Proton Mail is deliberately absent: it needs the Proton Bridge on the same
host, which speaks STARTTLS on localhost, and the watcher only does implicit
TLS or plain.
"""
from __future__ import annotations

from typing import Any, Dict, List

#: In the order the form lists them.
PRESETS: List[Dict[str, Any]] = [
    {
        "id": "gmail", "label": "Gmail / Google Workspace", "domains": ["gmail.com", "googlemail.com"],
        "imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_security": "starttls",
        "auth": "app_password", "help_url": "https://support.google.com/accounts/answer/185833",
    },
    {
        "id": "outlook", "label": "Outlook.com / Microsoft 365", "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_security": "starttls",
        "auth": "oauth", "help_url": "https://support.microsoft.com/office/d088b986-291d-42b8-9564-9c414e2aa040",
    },
    {
        "id": "yahoo", "label": "Yahoo Mail", "domains": ["yahoo.com", "ymail.com", "rocketmail.com"],
        "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_security": "ssl",
        "auth": "app_password", "help_url": "https://help.yahoo.com/kb/SLN15241.html",
    },
    {
        "id": "icloud", "label": "iCloud Mail", "domains": ["icloud.com", "me.com", "mac.com"],
        "imap_host": "imap.mail.me.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls",
        "auth": "app_password", "help_url": "https://support.apple.com/102654",
    },
    {
        "id": "yandex", "label": "Yandex Mail / Yandex 360", "domains": ["yandex.ru", "yandex.com", "ya.ru"],
        "imap_host": "imap.yandex.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.yandex.com", "smtp_port": 465, "smtp_security": "ssl",
        "auth": "app_password", "help_url": "https://yandex.com/support/id/authorization/app-passwords.html",
    },
    {
        "id": "mailru", "label": "Mail.ru / VK WorkMail", "domains": ["mail.ru", "bk.ru", "inbox.ru", "list.ru", "internet.ru"],
        "imap_host": "imap.mail.ru", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.ru", "smtp_port": 465, "smtp_security": "ssl",
        "auth": "app_password", "help_url": "https://help.mail.ru/mail/security/protection/external",
    },
    {
        "id": "fastmail", "label": "Fastmail", "domains": ["fastmail.com", "fastmail.fm"],
        "imap_host": "imap.fastmail.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.fastmail.com", "smtp_port": 465, "smtp_security": "ssl",
        "auth": "app_password", "help_url": "https://www.fastmail.help/hc/en-us/articles/360058752854",
    },
    {
        "id": "zoho", "label": "Zoho Mail", "domains": ["zohomail.com", "zoho.com"],
        "imap_host": "imap.zoho.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.zoho.com", "smtp_port": 465, "smtp_security": "ssl",
        "auth": "app_password", "help_url": "https://www.zoho.com/mail/help/imap-access.html",
    },
    {
        "id": "gmx", "label": "GMX", "domains": ["gmx.net", "gmx.de", "gmx.com", "gmx.at", "gmx.ch"],
        "imap_host": "imap.gmx.net", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "mail.gmx.net", "smtp_port": 587, "smtp_security": "starttls",
        "auth": "password", "help_url": "https://support.gmx.com/pop-imap/toggle.html",
    },
    {
        "id": "webde", "label": "WEB.DE", "domains": ["web.de"],
        "imap_host": "imap.web.de", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.web.de", "smtp_port": 587, "smtp_security": "starttls",
        "auth": "password", "help_url": "https://hilfe.web.de/pop-imap/einschalten.html",
    },
]


def find(preset_id: str) -> Dict[str, Any] | None:
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def for_address(address: str) -> Dict[str, Any] | None:
    """The preset whose domains include the address's domain, if any."""
    domain = (address or "").rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return None
    for p in PRESETS:
        if domain in p["domains"]:
            return p
    return None


def watcher_config(preset: Dict[str, Any]) -> Dict[str, Any]:
    """The ``imap`` watcher fields a preset fills (watchers/kinds.py)."""
    return {"host": preset["imap_host"], "port": preset["imap_port"], "ssl": preset["imap_ssl"]}


def channel_config(preset: Dict[str, Any]) -> Dict[str, Any]:
    """The mail channel fields a preset fills (connectors/mail/__init__.py)."""
    return {
        "imap_host": preset["imap_host"], "imap_port": preset["imap_port"],
        "imap_ssl": "yes" if preset["imap_ssl"] else "no",
        "smtp_host": preset["smtp_host"], "smtp_port": preset["smtp_port"],
        "smtp_security": preset["smtp_security"],
    }


def public() -> List[Dict[str, Any]]:
    """What the two forms receive: each preset with both field sets spelled out."""
    return [
        {
            "id": p["id"], "label": p["label"], "domains": list(p["domains"]),
            "auth": p["auth"], "help_url": p["help_url"],
            "watcher": watcher_config(p), "channel": channel_config(p),
        }
        for p in PRESETS
    ]


__all__ = ["PRESETS", "find", "for_address", "watcher_config", "channel_config", "public"]
