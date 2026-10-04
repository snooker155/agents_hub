"""
Google Workspace credential connector: Drive, Docs, Sheets and Calendar
behind one set of stored credentials, the ``connectors/credentials.py``
shape used by Jira, Linear, Microsoft Graph, Notion and the rest.

Two ways to authenticate, the operator picks one on the Connectors page:

* a service account (``service_account_json``, the JSON key file pasted in),
  which signs its own JWT grant, no person involved;
* an OAuth client (``client_id`` / ``client_secret``) the operator connects
  by clicking "Connect Google", which drives the three-legged flow in
  ``dashboard/backend/routes/google.py`` and lands a refresh token in
  ``refresh_token`` plus the connected account's address in
  ``account_email``. Both are written by the callback only, never typed in,
  so they carry ``kind="hidden"`` on their :class:`ConfigField`.

``connectors/google/auth.py`` turns whichever of those is present into a
bearer access token; ``connectors/google/client.py`` spends it. This module
only holds the stored config and the :class:`CredentialSpec` the generic
``/api/connectors/google`` routes and the Connectors page read.

Configured here means "either a service account or a connected OAuth client
is present" — an OR, not the all-of-``secret_fields`` check
:meth:`connectors.credentials.CredentialSpec.is_configured` does by default.
``required=()`` opts out of that default, and the instance's ``is_configured``
is replaced below with the OR version, so both the generic route and
``tools/google_workspace.py`` (through the module-level :func:`is_configured`
here) agree on what "configured" means.
"""
from __future__ import annotations

from typing import Any, Optional

from connectors.channels.registry import ConfigField
from connectors.channels.store import ChannelStore, in_workspace
from connectors.credentials import CredentialSpec

#: One document per workspace that defines the connector, three secrets each:
#: see the module docstring for the two modes. Follows the running code's
#: workspace (connectors/channels/store.py); :func:`store_for` binds one.
STORE = ChannelStore("google", secret_fields=("service_account_json", "client_secret", "refresh_token"))


def store_for(workspace: Optional[str] = None) -> ChannelStore:
    """The Google connector in effect for ``workspace``, bound to that one
    document: the workspace's own when it defines one, else the default
    workspace's. ``None`` means the running code's workspace (a run's
    context var). Binding once keeps every field of one call (client id,
    secret, refresh token) from the same document."""
    if workspace is None:
        return STORE.for_workspace(STORE.effective_workspace())
    return STORE.for_workspace(in_workspace(workspace, STORE.effective_workspace))

FIELDS = [
    ConfigField(
        key="service_account_json", secret=True, kind="textarea",
        placeholder="Paste the service account's JSON key file here",
    ),
    ConfigField(
        key="client_id", secret=False, kind="text",
        placeholder="OAuth client id (for Connect Google)",
    ),
    ConfigField(
        key="client_secret", secret=True, kind="text",
        placeholder="OAuth client secret",
    ),
    # Written by the OAuth callback only; never a form input.
    ConfigField(key="refresh_token", secret=True, kind="hidden"),
    ConfigField(key="account_email", secret=False, kind="hidden"),
    # The scopes the account granted, space separated, as Google's token
    # response lists them; says whether Gmail (IMAP/SMTP XOAUTH2) is allowed.
    ConfigField(key="granted_scopes", secret=False, kind="hidden"),
]


def mode(workspace: Optional[str] = None) -> str:
    """"service_account", "oauth" or "none" — a service account wins when both
    happen to be configured, since it needs no person to stay connected."""
    cfg = store_for(workspace).get_config()
    if str(cfg.get("service_account_json") or "").strip():
        return "service_account"
    if str(cfg.get("refresh_token") or "").strip():
        return "oauth"
    return "none"


def is_configured(workspace: Optional[str] = None) -> bool:
    """Either a service account or a connected OAuth client is present.

    The module-level function ``tools/google_workspace.py`` calls directly,
    and what :data:`CREDENTIALS` is patched to answer for the Connectors
    page and the generic ``/api/connectors/google`` routes (see the module
    docstring for why the default all-of-``secret_fields`` check is wrong
    here).
    """
    return mode(workspace) != "none"


def _test() -> dict[str, Any]:
    from .auth import GoogleError, get_access_token
    from .client import GoogleClient

    if not is_configured():
        return {"ok": False, "error": "Google connector is not configured"}
    try:
        token = get_access_token()
        client = GoogleClient(token)
        resp = client.get("https://www.googleapis.com/drive/v3/about", params={"fields": "user"})
        data = resp.json()
        email = str(((data.get("user") or {}).get("emailAddress")) or "")
        return {"ok": True, "identity": email}
    except GoogleError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        return {"ok": False, "error": str(exc)[:300]}


def _extra() -> dict[str, Any]:
    from .auth import has_gmail

    store = store_for()
    cfg = store.get_config()
    client_id = str(cfg.get("client_id") or "").strip()
    client_secret = str(cfg.get("client_secret") or "").strip()
    return {
        "mode": mode(),
        "account_email": str(cfg.get("account_email") or ""),
        "oauth_ready": bool(client_id and client_secret),
        "gmail": has_gmail(),
    }


CREDENTIALS = CredentialSpec(
    name="google",
    store=STORE,
    fields=FIELDS,
    test=_test,
    extra=_extra,
    required=(),
)
# See the module docstring: the default all-of-secret_fields check does not
# fit a connector with two independent ways to be configured, so the
# instance's is_configured is replaced with the OR version above.
CREDENTIALS.is_configured = is_configured

__all__ = ["CREDENTIALS", "STORE", "FIELDS", "mode", "is_configured", "store_for"]
