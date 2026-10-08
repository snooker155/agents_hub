"""
The model provider settings in force now, for this process and the ones it starts.

The backend reads ``.env`` into its environment once, at its start
(dashboard/backend/main.py), and every agent process and runner replica
starts from a copy of that environment. A key typed on the Settings page, in
the welcome window or into the assistant's connection card lands in ``.env``
(routes/settings.py), so without this module it would reach nothing until a
restart: the chat turns of a long-lived runner replica would go on without a
key, or with the old one.

So after a write, :func:`sync_process` copies the provider settings of the
file into this process (its environment and the shared ``settings``
object); :func:`overlay` puts the file's values into an environment about to
be handed to a child; and :func:`stamp` names them, so a replica started
with other values is replaced like one started from other code
(services/replicas.py ``is_stale``).

Only values set in the file count: an install that passes its keys through
the container environment and keeps them out of ``.env`` sees no change.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Dict, MutableMapping, Optional

log = logging.getLogger(__name__)

#: The settings a model call reads at the moment it is made.
PROVIDER_ENV_KEYS = (
    "DEFAULT_PROVIDER",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "GOOGLE_API_KEY", "GOOGLE_MODEL",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "LMSTUDIO_BASE_URL", "LMSTUDIO_MODEL",
    "WEB_SEARCH_PROVIDER", "WEB_SEARCH_API_KEY",
)

#: ``settings`` attribute for each key it mirrors (common/config.py).
_SETTINGS_FIELD = {
    "DEFAULT_PROVIDER": "default_provider",
    "OPENAI_API_KEY": "openai_api_key",
    "OPENAI_MODEL": "model",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "ANTHROPIC_MODEL": "anthropic_model",
    "GOOGLE_API_KEY": "google_api_key",
    "GOOGLE_MODEL": "google_model",
    "OLLAMA_BASE_URL": "ollama_base_url",
    "OLLAMA_MODEL": "ollama_model",
    "LMSTUDIO_BASE_URL": "lmstudio_base_url",
    "LMSTUDIO_MODEL": "lmstudio_model",
    "WEB_SEARCH_PROVIDER": "web_search_provider",
    "WEB_SEARCH_API_KEY": "web_search_api_key",
}


#: The file read and written; None is the project's ``.env``
#: (common/dotenv.py ``env_path``). The test suite points it at a file of its own.
ENV_FILE: Optional[Path] = None


def env_file() -> Path:
    if ENV_FILE is not None:
        return Path(ENV_FILE)
    from common import dotenv
    return dotenv.env_path()


def from_file(path: Optional[Path] = None) -> Dict[str, str]:
    """The provider settings the file sets to a value, never raises."""
    try:
        from common.dotenv import read_env
        env = read_env(Path(path) if path is not None else env_file())
    except Exception:  # noqa: BLE001 - an unreadable file sets nothing
        return {}
    return {k: str(env[k]).strip() for k in PROVIDER_ENV_KEYS if str(env.get(k) or "").strip()}


def live() -> Dict[str, str]:
    """The provider settings in force: the file's over this process's environment."""
    out = {k: os.environ[k].strip() for k in PROVIDER_ENV_KEYS if (os.environ.get(k) or "").strip()}
    out.update(from_file())
    return out


def live_value(key: str, default: str = "") -> str:
    return live().get(key, default)


def overlay(env: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """``env`` with the file's provider settings over it, for a child process."""
    env.update(from_file())
    return env


def sync_process(path: Optional[Path] = None) -> Dict[str, str]:
    """Copy the file's provider settings into this process. Returns what changed."""
    changed: Dict[str, str] = {}
    try:
        from common.config import settings
    except Exception:  # noqa: BLE001 - nothing to mirror into
        settings = None
    for key, value in from_file(path).items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            changed[key] = value
        field = _SETTINGS_FIELD.get(key)
        if settings is not None and field and getattr(settings, field, None) != value:
            try:
                setattr(settings, field, value)
            except Exception:  # noqa: BLE001 - a frozen or typed field keeps its value
                log.debug("provider env: settings.%s not updated", field, exc_info=True)
    return changed


def save(updates: Dict[str, str]) -> Dict[str, str]:
    """Write ``updates`` into ``.env`` (as the Settings page does, keeping every
    other line) and apply them here at once. Returns what changed in this process."""
    import re
    from common import dotenv

    path = env_file()
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, value in updates.items():
        escaped = str(value).replace('"', '\\"')
        line = f'{key}="{escaped}"'
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        if pattern.search(content):
            # A function, so a backslash in the value is not read as a group reference.
            content = pattern.sub(lambda _m, line=line: line, content)
        else:
            content = content.rstrip("\n") + ("\n" if content else "") + line + "\n"
    path.write_text(content, encoding="utf-8")
    dotenv.invalidate(path)
    return sync_process(path)


def stamp() -> str:
    """A short name for the provider settings in force (values hashed, never shown)."""
    digest = hashlib.sha256()
    current = live()
    for key in PROVIDER_ENV_KEYS:
        digest.update(key.encode())
        digest.update(b"=")
        digest.update(current.get(key, "").encode())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


__all__ = ["ENV_FILE", "PROVIDER_ENV_KEYS", "env_file", "from_file", "live", "live_value", "overlay", "save",
           "stamp", "sync_process"]
