"""
Knowledge base connectors: Notion and Confluence Cloud, as credential
connectors (connectors/credentials.py) — a token the hub reaches out to,
with no inbound loop, the same shape connectors/microsoft and
connectors/trackers use. The clients that actually talk to the two APIs
live in connectors/knowledge/notion.py and connectors/knowledge/confluence.py;
this module only wires their stored config to the generic
/api/connectors/notion and /api/connectors/confluence routes, and to the
``test`` callback each one shows on the Connectors page.

Each lives in the workspace that defines it, the default workspace's
everywhere else (connectors/channels/store.py). :func:`bound` picks the one
in effect for a workspace, so a client is built from one document.
"""
from __future__ import annotations

from typing import Any, Optional

from connectors.channels.registry import ConfigField
from connectors.channels.store import ChannelStore, in_workspace
from connectors.credentials import CredentialSpec

NOTION_STORE = ChannelStore("notion", secret_fields=("api_token",))
CONFLUENCE_STORE = ChannelStore("confluence", secret_fields=("api_token",))


def bound(store: ChannelStore, workspace: Optional[str] = None) -> ChannelStore:
    """``store``'s document in effect for ``workspace`` (the running code's
    when None): the workspace's own when it defines one, else the default's."""
    if workspace is None:
        return store.for_workspace(store.effective_workspace())
    return store.for_workspace(in_workspace(workspace, store.effective_workspace))


def notion_client(workspace: Optional[str] = None):
    """A ``NotionClient`` with the Notion connector in effect in ``workspace``."""
    from .notion import NotionClient
    return NotionClient(bound(NOTION_STORE, workspace).get("api_token"))


def confluence_client(workspace: Optional[str] = None):
    """A ``ConfluenceClient`` with the Confluence connector in effect in
    ``workspace``. Raises ``ConfluenceError`` for a refused base URL."""
    from .confluence import ConfluenceClient
    store = bound(CONFLUENCE_STORE, workspace)
    return ConfluenceClient(store.get("base_url"), store.get("email"), store.get("api_token"))


def _notion_test() -> dict[str, Any]:
    from .notion import NotionError

    try:
        client = notion_client()
        return client.test_connection()
    except NotionError as exc:
        return {"ok": False, "error": str(exc)}


def _confluence_test() -> dict[str, Any]:
    from .confluence import ConfluenceError

    try:
        client = confluence_client()
        return client.test_connection()
    except ConfluenceError as exc:
        return {"ok": False, "error": str(exc)}


NOTION = CredentialSpec(
    name="notion",
    store=NOTION_STORE,
    fields=[
        ConfigField("api_token", secret=True, kind="password", required=True,
                    placeholder="Internal integration token (ntn_... or secret_...)"),
    ],
    test=_notion_test,
    required=("api_token",),
)

CONFLUENCE = CredentialSpec(
    name="confluence",
    store=CONFLUENCE_STORE,
    fields=[
        ConfigField("base_url", required=True, placeholder="https://acme.atlassian.net/wiki"),
        ConfigField("email", required=True, placeholder="you@company.com"),
        ConfigField("api_token", secret=True, kind="password", required=True),
    ],
    test=_confluence_test,
    required=("base_url", "email", "api_token"),
)

CREDENTIALS_LIST = [NOTION, CONFLUENCE]

__all__ = ["NOTION", "CONFLUENCE", "CREDENTIALS_LIST", "NOTION_STORE", "CONFLUENCE_STORE", "bound",
           "notion_client", "confluence_client"]
