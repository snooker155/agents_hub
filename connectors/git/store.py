"""
Git connector configuration — file-backed token storage.

State lives in `.agents_hub/git_connectors.json`:

    {
        "github": {"token": "<secret, never returned to the UI>"},
        "gitlab": {"token": "<secret>", "base_url": "https://gitlab.com"}
    }

Tokens are write-only from the API perspective — callers see only
`has_token: bool`. The GitLab base URL is configurable for self-hosted
instances.
"""
from __future__ import annotations

import json
import os
from typing import Any

from filelock import FileLock

from common.paths import AGENTS_HUB_ROOT, ensure_agents_hub_root


PROVIDERS = ("github", "gitlab")

DEFAULT_GITLAB_BASE_URL = "https://gitlab.com"

_GIT_FILE = AGENTS_HUB_ROOT / "git_connectors.json"
_GIT_LOCK = AGENTS_HUB_ROOT / "git_connectors.json.lock"


def _default_state() -> dict[str, Any]:
    return {
        "github": {"token": ""},
        "gitlab": {"token": "", "base_url": DEFAULT_GITLAB_BASE_URL},
    }


def _load_unlocked() -> dict[str, Any]:
    if not _GIT_FILE.exists():
        return _default_state()
    try:
        txt = _GIT_FILE.read_text(encoding="utf-8")
        data = json.loads(txt) if txt.strip() else {}
    except Exception:
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    out = _default_state()
    for provider in PROVIDERS:
        if isinstance(data.get(provider), dict):
            out[provider].update(data[provider])
    return out


def _save_unlocked(data: dict[str, Any]) -> None:
    ensure_agents_hub_root()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = _GIT_FILE.with_suffix(_GIT_FILE.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, _GIT_FILE)


def _check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown git provider: {provider!r}")
    return provider


def load() -> dict[str, Any]:
    """Return the full state dict (tokens included; internal use only)."""
    with FileLock(str(_GIT_LOCK), timeout=5.0):
        return _load_unlocked()


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
    with FileLock(str(_GIT_LOCK), timeout=5.0):
        data = _load_unlocked()
        data[provider]["token"] = (token or "").strip()
        _save_unlocked(data)


def set_base_url(provider: str, base_url: str | None) -> None:
    if provider != "gitlab":
        raise ValueError("base_url is only configurable for gitlab")
    with FileLock(str(_GIT_LOCK), timeout=5.0):
        data = _load_unlocked()
        data["gitlab"]["base_url"] = (base_url or DEFAULT_GITLAB_BASE_URL).strip().rstrip("/")
        _save_unlocked(data)


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
