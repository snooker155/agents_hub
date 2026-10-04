"""
What an agent may ask an end user for: the providers, a small fixed set of
access kinds per provider, and how each is said in plain words.

The set is fixed on purpose. An operator picks from it on the agent's card
(``consent_settings.scopes``) and the agent's tool call names only the
provider, so neither the operator's typo nor the model's imagination can put
an unreviewed OAuth scope in front of an end user. Every key maps to the
provider's own scope strings plus the identity scopes the callback needs to
name the account.

The plain words are shown on the public consent page in the end user's
language (English, Russian or German, by ``Accept-Language``) and on the
agent's card in the dashboard (i18n namespace ``consent``).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

GOOGLE = "google"
MICROSOFT = "microsoft"
PROVIDERS: Tuple[str, ...] = (GOOGLE, MICROSOFT)

#: The personal secret each provider's grant is stored under (common/secrets.py,
#: user scope = the end user's principal, agent scope = the agent).
SECRET_NAMES: Dict[str, str] = {GOOGLE: "CONSENT_GOOGLE", MICROSOFT: "CONSENT_MICROSOFT"}

PROVIDER_LABELS: Dict[str, str] = {GOOGLE: "Google", MICROSOFT: "Microsoft"}

_G = "https://www.googleapis.com/auth/"

#: key -> provider scope strings.
SCOPES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    GOOGLE: {
        "calendar": (_G + "calendar",),
        "calendar_read": (_G + "calendar.readonly",),
        "drive_read": (_G + "drive.readonly",),
        "drive": (_G + "drive",),
        "docs": (_G + "documents",),
        "sheets": (_G + "spreadsheets",),
        "mail_read": (_G + "gmail.readonly",),
    },
    MICROSOFT: {
        "calendar": ("Calendars.ReadWrite",),
        "calendar_read": ("Calendars.Read",),
        "mail_read": ("Mail.Read",),
        "mail_send": ("Mail.Send",),
        "files_read": ("Files.Read",),
        "files": ("Files.ReadWrite",),
    },
}

#: Always asked for: who the account is (the address shown to the operator
#: and on the done page), and for Microsoft the refresh token itself.
IDENTITY_SCOPES: Dict[str, Tuple[str, ...]] = {
    GOOGLE: ("openid", _G + "userinfo.email"),
    MICROSOFT: ("openid", "email", "offline_access", "User.Read"),
}

#: What an agent with the provider switched on but no keys picked asks for.
DEFAULT_KEYS: Dict[str, Tuple[str, ...]] = {GOOGLE: ("calendar",), MICROSOFT: ("calendar",)}

LANGUAGES = ("en", "ru", "de")

#: key -> {language: plain words}. Shared by both providers where it reads
#: the same.
SCOPE_LABELS: Dict[str, Dict[str, str]] = {
    "calendar": {
        "en": "See and change the events in your calendar",
        "ru": "Видеть и изменять события в вашем календаре",
        "de": "Termine in Ihrem Kalender sehen und ändern",
    },
    "calendar_read": {
        "en": "See the events in your calendar",
        "ru": "Видеть события в вашем календаре",
        "de": "Termine in Ihrem Kalender sehen",
    },
    "drive_read": {
        "en": "See and download your Google Drive files",
        "ru": "Видеть и скачивать ваши файлы в Google Диске",
        "de": "Ihre Google Drive Dateien sehen und herunterladen",
    },
    "drive": {
        "en": "See, change, create and delete your Google Drive files",
        "ru": "Видеть, изменять, создавать и удалять ваши файлы в Google Диске",
        "de": "Ihre Google Drive Dateien sehen, ändern, anlegen und löschen",
    },
    "docs": {
        "en": "See, change and create your Google Docs",
        "ru": "Видеть, изменять и создавать ваши Google Документы",
        "de": "Ihre Google Docs sehen, ändern und anlegen",
    },
    "sheets": {
        "en": "See, change and create your Google Sheets",
        "ru": "Видеть, изменять и создавать ваши Google Таблицы",
        "de": "Ihre Google Tabellen sehen, ändern und anlegen",
    },
    "mail_read": {
        "en": "Read your email",
        "ru": "Читать вашу почту",
        "de": "Ihre E-Mails lesen",
    },
    "mail_send": {
        "en": "Send email as you",
        "ru": "Отправлять письма от вашего имени",
        "de": "E-Mails in Ihrem Namen senden",
    },
    "files_read": {
        "en": "See and download your OneDrive files",
        "ru": "Видеть и скачивать ваши файлы в OneDrive",
        "de": "Ihre OneDrive Dateien sehen und herunterladen",
    },
    "files": {
        "en": "See, change, create and delete your OneDrive files",
        "ru": "Видеть, изменять, создавать и удалять ваши файлы в OneDrive",
        "de": "Ihre OneDrive Dateien sehen, ändern, anlegen und löschen",
    },
}


def is_provider(name: str) -> bool:
    return str(name or "").strip().lower() in PROVIDERS


def keys_for(provider: str) -> List[str]:
    """The access keys a provider offers, in catalog order."""
    return list(SCOPES.get(provider, {}).keys())


def clean_keys(provider: str, keys) -> List[str]:
    """``keys`` reduced to the provider's catalog, in catalog order, no
    duplicates. Raises ValueError naming an unknown key."""
    offered = SCOPES.get(provider)
    if offered is None:
        raise ValueError(f"unknown provider '{provider}'")
    wanted = [str(k or "").strip() for k in (keys or []) if str(k or "").strip()]
    for key in wanted:
        if key not in offered:
            raise ValueError(f"'{key}' is not an access kind {PROVIDER_LABELS[provider]} offers "
                             f"(one of: {', '.join(offered)})")
    return [k for k in offered if k in wanted]


def provider_scopes(provider: str, keys) -> List[str]:
    """The OAuth scope strings for ``keys``, identity scopes included."""
    out: List[str] = []
    for key in keys:
        for scope in SCOPES[provider].get(key, ()):
            if scope not in out:
                out.append(scope)
    for scope in IDENTITY_SCOPES[provider]:
        if scope not in out:
            out.append(scope)
    return out


def label(key: str, lang: str = "en") -> str:
    words = SCOPE_LABELS.get(key) or {}
    return words.get(lang) or words.get("en") or key


__all__ = [
    "DEFAULT_KEYS", "GOOGLE", "IDENTITY_SCOPES", "LANGUAGES", "MICROSOFT", "PROVIDERS",
    "PROVIDER_LABELS", "SCOPES", "SCOPE_LABELS", "SECRET_NAMES", "clean_keys", "is_provider",
    "keys_for", "label", "provider_scopes",
]
