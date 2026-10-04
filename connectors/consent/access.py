"""
An end user's grant: stored, read, spent in their own turns, and taken back.

The grant is a personal secret (common/secrets.py): name ``CONSENT_GOOGLE`` or
``CONSENT_MICROSOFT``, workspace and agent scope of the agent that asked, user
scope = the end user's principal (``widget:<widget>:<visitor>`` or
``channel:<channel>:<chat>``). Its value is a small JSON document: the refresh
token, the account's address, the scopes granted, and the request that
produced it. It is encrypted like any other secret, never listed with its
value, and never handed to a run's environment: a run's ``user_id`` is a hub
account, never a principal, so the allowlist lookup cannot reach it.

Spending it. A Google or Microsoft tool in a turn asks :func:`token_for_turn`
(or :func:`graph_client_for_turn`) before the hub's own credentials:

- the turn has an end user and they granted this agent the provider: their
  access token, refreshed from the refresh token as needed and cached per
  principal until shortly before it expires;
- the turn has an end user, no grant, and the agent's consent settings name
  the provider: :class:`ConsentError`, "ask for access first". The hub's own
  account is never used for a call the end user's consent was meant to cover;
- otherwise (no end user, or an agent not set up for it): None, and the
  caller goes on with the hub-wide connection as before.

The cache is keyed by the secret's ``updated_at`` as well as the scope, so a
grant replaced or revoked on another replica is noticed at the next call
without decrypting anything.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from common import secrets as secrets_mod

from . import catalog, store

log = logging.getLogger(__name__)

ASK_FIRST = ("This agent acts on {label} only as the person in this conversation, and they have "
             "not given it access yet. Call request_account_access with provider '{provider}' "
             "and send them the link it returns.")
ENDED = ("The person's {label} access has ended (they or the provider revoked it). Call "
         "request_account_access with provider '{provider}' to ask again.")


class ConsentError(Exception):
    """A tool call the end user's consent must cover cannot go ahead. The
    message is meant for the agent and safe to show."""


_lock = threading.Lock()
#: (provider, workspace, agent_id, principal) -> (secret updated_at, access token, expiry)
_TOKENS: Dict[Tuple[str, str, str, str], Tuple[str, str, float]] = {}


def reset_cache() -> None:
    with _lock:
        _TOKENS.clear()


def _forget(key: Tuple[str, str, str, str]) -> None:
    with _lock:
        _TOKENS.pop(key, None)


# ── the turn ─────────────────────────────────────────────────────────────────

def turn_scope() -> Optional[Tuple[str, str, str]]:
    """``(workspace, agent_id, principal)`` of a widget or channel turn, else None."""
    principal = secrets_mod.current_end_user()
    scope = secrets_mod.active_scope()
    if not principal or not scope or not scope[0] or not scope[1]:
        return None
    return scope[0], scope[1], principal


def agent_providers(agent_id: str) -> list:
    try:
        return list(store.get_settings(agent_id).get("providers") or [])
    except Exception:  # noqa: BLE001 - unreadable settings: nothing is required, the hub's connection applies
        log.warning("consent: settings of %s unreadable", agent_id, exc_info=True)
        return []


def has_grant(workspace: str, agent_id: str, principal: str, provider: str) -> bool:
    return secrets_mod.secret_updated_at(workspace, catalog.SECRET_NAMES[provider],
                                         agent_id=agent_id, user_id=principal) is not None


def mode_for_turn(provider: str) -> Optional[str]:
    """``"end_user"`` (a grant applies), ``"required"`` (the agent may only act
    as the end user and has no grant) or None (the hub's connection applies)."""
    scope = turn_scope()
    if scope is None:
        return None
    workspace, agent_id, principal = scope
    if has_grant(workspace, agent_id, principal, provider):
        return "end_user"
    if provider in agent_providers(agent_id):
        return "required"
    return None


# ── the grant ────────────────────────────────────────────────────────────────

def read_grant(workspace: str, agent_id: str, principal: str,
               provider: str) -> Optional[Dict[str, Any]]:
    raw = secrets_mod.get_secret(workspace, catalog.SECRET_NAMES[provider], agent_id=agent_id,
                                 user_id=principal)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("refresh_token") else None


def store_grant(row: Dict[str, Any], *, refresh_token: str, account_email: str,
                granted_scopes: list) -> None:
    """Keep a fresh grant as the end user's personal secret."""
    value = json.dumps({
        "refresh_token": refresh_token,
        "account_email": account_email,
        "scopes": list(granted_scopes),
        "request_id": row["request_id"],
        "granted_at": datetime.now(timezone.utc).isoformat(),
    })
    secrets_mod.set_secret(row["workspace"], catalog.SECRET_NAMES[row["provider"]], value,
                           agent_id=row["agent_id"], user_id=row["principal"],
                           created_by=f"consent:{row['request_id']}")
    _forget((row["provider"], row["workspace"], row["agent_id"], row["principal"]))


def _rewrite_refresh(workspace: str, agent_id: str, principal: str, provider: str,
                     grant: Dict[str, Any], new_refresh: str) -> None:
    """The identity platform rotated the refresh token: keep the new one."""
    value = json.dumps({**grant, "refresh_token": new_refresh})
    try:
        secrets_mod.set_secret(workspace, catalog.SECRET_NAMES[provider], value,
                               agent_id=agent_id, user_id=principal)
    except Exception:  # noqa: BLE001 - the old token still works until it expires; log, go on
        log.warning("consent: could not keep a rotated %s refresh token", provider, exc_info=True)


def _refresh(provider: str, grant: Dict[str, Any],
             workspace: Optional[str] = None) -> Tuple[str, float, Optional[str]]:
    """``(access_token, expiry, rotated refresh token or None)``, with the app
    registration of ``workspace`` (the grant's: its own connector, else the
    default's), the one that issued the refresh token. Raises the provider's
    ``*GrantRevoked`` when the grant is dead."""
    if provider == catalog.GOOGLE:
        from connectors.google.auth import refresh_user_token
        token, expiry = refresh_user_token(grant["refresh_token"], workspace=workspace)
        return token, expiry, None
    from connectors.microsoft.graph import refresh_delegated
    scopes = list(grant.get("scopes") or catalog.provider_scopes(provider, catalog.DEFAULT_KEYS[provider]))
    data = refresh_delegated(grant["refresh_token"], scopes, workspace=workspace)
    expiry = time.time() + float(data.get("expires_in") or 3600) - 60
    rotated = data.get("refresh_token")
    return str(data["access_token"]), expiry, (str(rotated) if rotated and rotated != grant["refresh_token"] else None)


def _grant_dead(exc: Exception) -> bool:
    from connectors.google.auth import GoogleGrantRevoked
    from connectors.microsoft.graph import GraphGrantRevoked
    return isinstance(exc, (GoogleGrantRevoked, GraphGrantRevoked))


def access_token(workspace: str, agent_id: str, principal: str, provider: str) -> str:
    """The end user's access token, refreshed and cached. Raises
    :class:`ConsentError` when there is no grant or it ended, and the
    provider's own error (GoogleError, GraphError) for a passing failure."""
    label = catalog.PROVIDER_LABELS[provider]
    key = (provider, workspace, agent_id, principal)
    marker = secrets_mod.secret_updated_at(workspace, catalog.SECRET_NAMES[provider],
                                           agent_id=agent_id, user_id=principal)
    if marker is None:
        _forget(key)
        raise ConsentError(ASK_FIRST.format(label=label, provider=provider))
    with _lock:
        cached = _TOKENS.get(key)
    if cached and cached[0] == marker and time.time() < cached[2]:
        return cached[1]
    grant = read_grant(workspace, agent_id, principal, provider)
    if grant is None:
        # A row that does not decrypt (the key changed) is as good as none.
        raise ConsentError(ASK_FIRST.format(label=label, provider=provider))
    try:
        token, expiry, rotated = _refresh(provider, grant, workspace)
    except Exception as exc:
        if _grant_dead(exc):
            revoke(workspace, agent_id, principal, provider, by="provider", call_provider=False)
            raise ConsentError(ENDED.format(label=label, provider=provider)) from exc
        raise
    if rotated:
        _rewrite_refresh(workspace, agent_id, principal, provider, grant, rotated)
        marker = secrets_mod.secret_updated_at(workspace, catalog.SECRET_NAMES[provider],
                                               agent_id=agent_id, user_id=principal) or marker
    with _lock:
        _TOKENS[key] = (marker, token, expiry)
    return token


def token_for_turn(provider: str) -> Optional[str]:
    """The end user's token for this turn, None when the hub's own connection
    applies. Raises :class:`ConsentError` when the end user must be asked."""
    mode = mode_for_turn(provider)
    if mode is None:
        return None
    workspace, agent_id, principal = turn_scope()  # type: ignore[misc]
    if mode == "required":
        raise ConsentError(ASK_FIRST.format(label=catalog.PROVIDER_LABELS[provider],
                                            provider=provider))
    return access_token(workspace, agent_id, principal, provider)


def graph_client_for_turn():
    """A Graph client acting as the end user, or None when the hub's own app
    applies. A refusal surfaces as a ``GraphError`` at the first call, where
    the Outlook tools already turn it into their error answer."""
    mode = mode_for_turn(catalog.MICROSOFT)
    if mode is None:
        return None
    from connectors.microsoft.graph import DelegatedGraphClient, GraphError

    workspace, agent_id, principal = turn_scope()  # type: ignore[misc]
    row = store.grant_row(workspace, agent_id, principal, catalog.MICROSOFT) if mode == "end_user" else None

    def _token() -> str:
        try:
            token = token_for_turn(catalog.MICROSOFT)
        except ConsentError as exc:
            raise GraphError(str(exc)) from exc
        if not token:
            raise GraphError(ASK_FIRST.format(label="Microsoft", provider=catalog.MICROSOFT))
        return token

    return DelegatedGraphClient(_token, (row or {}).get("account_email") or "")


# ── taking it back ───────────────────────────────────────────────────────────

def revoke(workspace: str, agent_id: str, principal: str, provider: str, *, by: str,
           call_provider: bool = True, actor: Optional[Dict[str, Any]] = None,
           principal_obj: Any = None) -> bool:
    """Delete the grant, ask the provider to revoke it where it can (Google),
    mark its request ``revoked`` and write an audit row. True when there was
    a grant to take back."""
    grant = None
    if call_provider and provider == catalog.GOOGLE:
        try:
            grant = read_grant(workspace, agent_id, principal, provider)
        except Exception:  # noqa: BLE001 - an unreadable grant is still deleted below
            grant = None
    deleted = secrets_mod.delete_secret(workspace, catalog.SECRET_NAMES[provider],
                                        agent_id=agent_id, user_id=principal)
    _forget((provider, workspace, agent_id, principal))
    provider_revoked = None
    if grant and provider == catalog.GOOGLE:
        from connectors.google.auth import revoke_token
        provider_revoked = revoke_token(grant["refresh_token"])
    rows = store.mark_revoked(workspace, agent_id, principal, provider, by=by)
    if deleted or rows:
        from common import audit
        audit.record("consent.revoke", principal=principal_obj, actor=actor,
                     object_type="consent", object_id=f"{agent_id}:{provider}",
                     workspace=workspace,
                     details={"principal": principal, "provider": provider, "by": by,
                              "provider_revoked": provider_revoked})
    return bool(deleted or rows)


def end_user_actor(principal: str, account_email: str = "") -> Dict[str, Any]:
    """The audit actor for something the end user did themselves."""
    return {"actor_id": principal, "actor_kind": "end_user", "actor_name": account_email or None}


__all__ = [
    "ASK_FIRST", "ConsentError", "access_token", "agent_providers", "end_user_actor",
    "graph_client_for_turn", "has_grant", "mode_for_turn", "read_grant", "reset_cache", "revoke",
    "store_grant", "token_for_turn", "turn_scope",
]
