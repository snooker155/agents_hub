"""
Git connector configuration, in the database.

State is one document, held by :class:`common.docstore.DocStore` under the
key ``"state"`` (store name ``"git_connectors"``), shaped like the old
``.agents_hub/git_connectors.json``:

    {
        "github": {"token": "<secret, never returned to the UI>"},
        "gitlab": {"token": "<secret>", "base_url": "https://gitlab.com"}
    }

Tokens are write-only from the API perspective — callers see only
`has_token: bool`. The GitLab base URL is configurable for self-hosted
instances.

Every setter below is a read-modify-write inside ``store.transaction()``,
atomic across every process and host, in place of the file lock this used to
take. An existing ``git_connectors.json`` is imported once on first use and
renamed ``.migrated``.
"""
from __future__ import annotations

import json

from typing import Any

from common.docstore import DocStore
from common.paths import AGENTS_HUB_ROOT


PROVIDERS = ("github", "gitlab")

DEFAULT_GITLAB_BASE_URL = "https://gitlab.com"

#: Legacy JSON file this collection was imported from.
_GIT_FILE = AGENTS_HUB_ROOT / "git_connectors.json"

# No ``legacy_file=`` here: git_connectors.json is one dict of settings, not a
# collection, so the store's own per-key import would split it into one
# document per provider. It is imported by hand, below, as a single document
# under the "state" key.
_store = DocStore("git_connectors")

_STATE_KEY = "state"


def _default_state() -> dict[str, Any]:
    return {
        "github": {"token": ""},
        "gitlab": {"token": "", "base_url": DEFAULT_GITLAB_BASE_URL},
    }


def _ensure_legacy_imported() -> None:
    """Import ``git_connectors.json`` once, as the single "state" document.

    A store that already has rows is left alone (:meth:`DocStore.import_legacy`
    re-checks this itself, atomically); the cheap existence check here just
    avoids reading and parsing the file on every call once it is gone.
    """
    if not _GIT_FILE.exists():
        return
    try:
        text = _GIT_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else None
    except Exception:
        return
    if isinstance(data, dict):
        _store.import_legacy({_STATE_KEY: data}, _GIT_FILE)


def _coerce(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _default_state()
    out = _default_state()
    for provider in PROVIDERS:
        if isinstance(data.get(provider), dict):
            out[provider].update(data[provider])
    return out


def _check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown git provider: {provider!r}")
    return provider


def load() -> dict[str, Any]:
    """Return the full state dict (tokens included; internal use only)."""
    _ensure_legacy_imported()
    return _coerce(_store.get(_STATE_KEY))


def get_config(provider: str) -> dict[str, Any]:
    """Return provider config including the raw token (internal use only)."""
    return dict(load().get(_check_provider(provider)) or {})


def get_token(provider: str) -> str:
    return str(get_config(provider).get("token") or "").strip()


def has_token(provider: str) -> bool:
    return bool(get_token(provider))


def get_base_url(provider: str) -> str:
    if provider == "gitlab":
        return str(get_config("gitlab").get("base_url") or DEFAULT_GITLAB_BASE_URL).rstrip("/")
    return "https://github.com"


def set_token(provider: str, token: str | None) -> None:
    """Set or clear (empty/None) the provider token."""
    _check_provider(provider)
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        data[provider]["token"] = (token or "").strip()
        _store.put(_STATE_KEY, data)


def set_base_url(provider: str, base_url: str | None) -> None:
    if provider != "gitlab":
        raise ValueError("base_url is only configurable for gitlab")
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        data["gitlab"]["base_url"] = (base_url or DEFAULT_GITLAB_BASE_URL).strip().rstrip("/")
        _store.put(_STATE_KEY, data)


def public_config() -> dict[str, Any]:
    """Config safe to return to the UI — tokens replaced by has_token flags."""
    state = load()
    return {
        "github": {"has_token": bool(str(state["github"].get("token") or "").strip())},
        "gitlab": {
            "has_token": bool(str(state["gitlab"].get("token") or "").strip()),
            "base_url": str(state["gitlab"].get("base_url") or DEFAULT_GITLAB_BASE_URL),
        },
    }
