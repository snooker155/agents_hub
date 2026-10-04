"""
Microsoft Graph credential connector (see ``connectors/credentials.py``).

A client-credentials Azure AD app (application permissions
``Calendars.ReadWrite`` and optionally ``User.Read.All``): no inbound loop,
just a token used by ``connectors/microsoft/graph.py`` and the tools in
``tools/microsoft_graph.py`` (the Outlook calendar). ``default_user`` is the
mailbox the tools act on when a tool call names none.

One app registration per workspace that defines the connector, the default
workspace's everywhere else (connectors/channels/store.py): :func:`store_for`
binds the one in effect for a workspace, so every field of one call comes
from the same document.
"""
from __future__ import annotations

from typing import Any, Optional

from connectors.channels.registry import ConfigField
from connectors.channels.store import ChannelStore, in_workspace
from connectors.credentials import CredentialSpec

_STORE = ChannelStore("microsoft", secret_fields=("client_secret",))
STORE = _STORE


def store_for(workspace: Optional[str] = None) -> ChannelStore:
    """The Microsoft connector in effect for ``workspace`` (the running
    code's when None), bound to that one document: the workspace's own when
    it defines one, else the default workspace's."""
    if workspace is None:
        return _STORE.for_workspace(_STORE.effective_workspace())
    return _STORE.for_workspace(in_workspace(workspace, _STORE.effective_workspace))


def _test() -> dict[str, Any]:
    from .graph import GraphClient, GraphError

    store = store_for()
    tenant_id = store.get("tenant_id")
    client_id = store.get("client_id")
    client_secret = store.get("client_secret")
    if not (tenant_id and client_id and client_secret):
        return {"ok": False, "error": "tenant_id, client_id and client_secret are required"}

    client = GraphClient(tenant_id, client_id, client_secret)
    default_user = store.get("default_user")
    try:
        if default_user:
            user = client.get(f"/users/{default_user}")
            identity = user.get("displayName") or user.get("userPrincipalName") or default_user
        else:
            org = client.get("/organization")
            values = org.get("value") or []
            identity = (values[0].get("displayName") if values else None) or tenant_id
        return {"ok": True, "identity": identity}
    except GraphError as exc:
        return {"ok": False, "error": str(exc)}


CREDENTIALS = CredentialSpec(
    name="microsoft",
    store=_STORE,
    fields=[
        ConfigField("tenant_id", kind="text", placeholder="Azure AD tenant id", required=True),
        ConfigField("client_id", kind="text", placeholder="App registration's client id", required=True),
        ConfigField("client_secret", secret=True, kind="password",
                    placeholder="App registration's client secret", required=True),
        ConfigField("default_user", kind="text",
                    placeholder="mailbox UPN or id used when a tool call names none"),
        # The sign-in authority for end users granting their own account
        # through the consent portal (docs/consent.md): common, organizations,
        # consumers or a tenant id. Empty: the app's own tenant.
        ConfigField("consent_tenant", kind="text",
                    placeholder="end user sign-in: common, organizations or a tenant id (empty: tenant_id)"),
    ],
    test=_test,
    required=("tenant_id", "client_id", "client_secret"),
)

__all__ = ["CREDENTIALS", "STORE", "store_for"]
