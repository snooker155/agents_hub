"""
Git connector configuration, in the database.

State is one document, held by :class:`common.docstore.DocStore` under the
key ``"state"`` (store name ``"git_connectors"``), shaped like the old
``.agents_hub/git_connectors.json``:

    {
        "gitlab": {"token": "<secret>", "base_url": "https://gitlab.com"}
    }

Tokens are write-only from the API perspective: callers see only
`has_token: bool`. The GitLab base URL is configurable for self-hosted
instances.

Every setter below is a read-modify-write inside ``store.transaction()``,
atomic across every process and host, in place of the file lock this used to
take. An existing ``git_connectors.json`` is imported once on first use and
renamed ``.migrated``.

Per workspace (docs/connectors.md "Connectors per workspace"). The document
above, under ``"state"``, is the default workspace's. Another workspace that
defines a provider of its own keeps it under ``"state@<workspace>"``, holding
only the providers it defines. A provider lives in the workspace that
defines it, and the default workspace's live everywhere: in workspace X a
read uses X's entry for that provider when X defines it, else the default's,
never another workspace's.

Every function takes an optional ``workspace``. Without one it is the running
code's workspace (connectors/store.py ``scope_workspace``: a run's
context var, then ``AGENT_WORKSPACE``, never the workspace a person selected
in the UI), so code inside a run needs nothing; a route or a background job
acting for a workspace names it.
"""
from __future__ import annotations

import json
import logging
import os

from typing import Any, Optional

from common.docstore import DocStore
from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger(__name__)


PROVIDERS = ("gitlab",)

DEFAULT_GITLAB_BASE_URL = "https://gitlab.com"

#: Legacy JSON file this collection was imported from.
_GIT_FILE = AGENTS_HUB_ROOT / "git_connectors.json"

# No ``legacy_file=`` here: git_connectors.json is one dict of settings, not a
# collection, so the store's own per-key import would split it into one
# document per provider. It is imported by hand, below, as a single document
# under the "state" key.
_store = DocStore("git_connectors")

_STATE_KEY = "state"

DEFAULT_WORKSPACE = "default"


def _norm_ws(workspace: Optional[str]) -> str:
    ws = str(workspace or "").strip()
    return ws or DEFAULT_WORKSPACE


def _key_for(workspace: Optional[str]) -> str:
    ws = _norm_ws(workspace)
    return _STATE_KEY if ws == DEFAULT_WORKSPACE else f"{_STATE_KEY}@{ws}"


def _running_workspace() -> str:
    from connectors.store import scope_workspace
    return _norm_ws(scope_workspace())


def _own(workspace: str) -> dict[str, Any]:
    """The raw document a workspace other than the default keeps: only the
    providers it defines."""
    data = _store.get(_key_for(workspace))
    return {p: dict(v) for p, v in data.items() if p in PROVIDERS and isinstance(v, dict)} \
        if isinstance(data, dict) else {}


def defines(provider: str, workspace: Optional[str]) -> bool:
    """Whether ``workspace`` defines ``provider`` itself (the default always does)."""
    _check_provider(provider)
    ws = _norm_ws(workspace)
    return ws == DEFAULT_WORKSPACE or provider in _own(ws)


def source_workspace(provider: str, workspace: Optional[str] = None) -> str:
    """The workspace whose entry for ``provider`` is in effect in
    ``workspace`` (the running code's when None)."""
    ws = _norm_ws(workspace) if workspace is not None else _running_workspace()
    return ws if ws != DEFAULT_WORKSPACE and defines(provider, ws) else DEFAULT_WORKSPACE


def defined_workspaces(provider: Optional[str] = None) -> list[str]:
    """The workspaces other than the default that define ``provider`` (any
    provider when None)."""
    prefix = f"{_STATE_KEY}@"
    out = []
    for key in _store.keys():
        if not key.startswith(prefix):
            continue
        ws = key[len(prefix):]
        own = _own(ws)
        if (provider is None and own) or (provider is not None and provider in own):
            out.append(ws)
    return sorted(out)


def remove_workspace(provider: str, workspace: Optional[str]) -> bool:
    """Drop ``workspace``'s own entry for ``provider``, so it uses the default
    workspace's again. The default's own cannot be removed."""
    _check_provider(provider)
    ws = _norm_ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        return False
    key = _key_for(ws)
    with _store.transaction():
        own = _own(ws)
        if provider not in own:
            return False
        own.pop(provider)
        if own:
            _store.put(key, own)
        else:
            _store.delete(key)
    return True


def _default_state() -> dict[str, Any]:
    return {
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
    except (OSError, ValueError):
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


def load(workspace: Optional[str] = None) -> dict[str, Any]:
    """The full state in effect in ``workspace`` (tokens included; internal
    use only): each provider from the workspace when it defines it, else
    from the default workspace."""
    _ensure_legacy_imported()
    out = _coerce(_store.get(_STATE_KEY))
    ws = _norm_ws(workspace) if workspace is not None else _running_workspace()
    if ws != DEFAULT_WORKSPACE:
        own = _coerce(_own(ws))
        for provider in _own(ws):
            out[provider] = own[provider]
    return out


def get_config(provider: str, workspace: Optional[str] = None) -> dict[str, Any]:
    """Return provider config including the raw token (internal use only)."""
    return dict(load(workspace).get(_check_provider(provider)) or {})


#: The environment variable a run's own token arrives in (common/secrets.py
#: hands workspace secrets to a run under their names).
TOKEN_ENV = {"gitlab": "GITLAB_TOKEN"}


def _run_token(provider: str, workspace: Optional[str] = None) -> str:
    """The token the current run holds for itself, or "".

    An agent with an agent-scoped ``GITLAB_TOKEN`` secret pushes and opens
    merge requests as its own identity rather than as the connector's. Inside
    the backend process (a chat turn) that token comes from the active secret
    scope; inside a run subprocess, from the environment the launcher built.
    The environment is only trusted in a run (``AGENT_WORKSPACE`` is set by
    common/subprocess_env.py), so a ``GITLAB_TOKEN`` the operator happens to
    export in the shell that starts the backend does not quietly replace the
    token configured in Settings.
    """
    name = TOKEN_ENV.get(provider)
    if not name:
        return ""
    try:
        from common import secrets as _secrets
        if _secrets.active_scope() is not None:
            # A secret bound to hosts (common/secrets.py) is only handed out
            # for the host it goes to: the provider's API.
            value = _secrets.get(name, host=_api_host(provider, workspace))
            if value:
                return value.strip()
    except Exception:  # noqa: BLE001 - a secret scope failure falls back to the environment
        log.debug("git token lookup through secrets failed", exc_info=True)
    if os.environ.get("AGENT_WORKSPACE"):
        return (os.environ.get(name) or "").strip()
    return ""


def _api_host(provider: str, workspace: Optional[str] = None) -> str:
    """The host a run token for ``provider`` is sent to."""
    try:
        from urllib.parse import urlsplit
        return (urlsplit(get_base_url(provider, workspace)).hostname or "").lower()
    except ValueError:
        return ""


def get_token(provider: str, workspace: Optional[str] = None) -> str:
    """The token to act with: the run's own, else the connector's in effect
    in ``workspace``."""
    return _run_token(provider, workspace) or str(get_config(provider, workspace).get("token") or "").strip()


def has_token(provider: str, workspace: Optional[str] = None) -> bool:
    return bool(get_token(provider, workspace))


def get_base_url(provider: str, workspace: Optional[str] = None) -> str:
    _check_provider(provider)
    return str(get_config(provider, workspace).get("base_url") or DEFAULT_GITLAB_BASE_URL).rstrip("/")


def _write(provider: str, workspace: Optional[str], fn) -> None:
    """Apply ``fn`` to ``provider``'s entry in one workspace's document,
    atomically. ``workspace`` None writes where a read would look (the
    running code's workspace when it defines the provider, else the
    default's); a named workspace other than the default gets an entry of its
    own (defining the provider there), starting from empty fields, never a
    copy of the default's token."""
    _check_provider(provider)
    _ensure_legacy_imported()
    ws = source_workspace(provider) if workspace is None else _norm_ws(workspace)
    key = _key_for(ws)
    with _store.transaction():
        if ws == DEFAULT_WORKSPACE:
            data = _coerce(_store.get(key))
            fn(data[provider])
            _store.put(key, data)
            return
        own = _own(ws)
        entry = _default_state()[provider]
        entry.update(own.get(provider) or {})
        fn(entry)
        own[provider] = entry
        _store.put(key, own)


def set_token(provider: str, token: str | None, workspace: Optional[str] = None) -> None:
    """Set or clear (empty/None) the provider token."""
    _write(provider, workspace, lambda e: e.__setitem__("token", (token or "").strip()))


def set_base_url(provider: str, base_url: str | None, workspace: Optional[str] = None) -> None:
    _check_provider(provider)
    value = (base_url or DEFAULT_GITLAB_BASE_URL).strip().rstrip("/")
    _write(provider, workspace, lambda e: e.__setitem__("base_url", value))


def public_config(workspace: Optional[str] = None) -> dict[str, Any]:
    """Config safe to return to the UI (tokens replaced by has_token flags)
    in effect in ``workspace`` (the default's when None outside a run).
    ``sources`` says, per provider, whether the workspace defines it
    ("here") or uses the default workspace's ("default")."""
    ws = _norm_ws(workspace) if workspace is not None else _running_workspace()
    state = load(ws)
    out: dict[str, Any] = {
        "workspace": ws,
        "sources": {p: ("here" if ws == DEFAULT_WORKSPACE or defines(p, ws) else DEFAULT_WORKSPACE)
                    for p in PROVIDERS},
        "gitlab": {
            "has_token": bool(str(state["gitlab"].get("token") or "").strip()),
            "base_url": str(state["gitlab"].get("base_url") or DEFAULT_GITLAB_BASE_URL),
        },
    }
    if ws == DEFAULT_WORKSPACE:
        # Which other workspaces bring their own, per provider.
        out["defined_in"] = {p: defined_workspaces(p) for p in PROVIDERS}
    return out
