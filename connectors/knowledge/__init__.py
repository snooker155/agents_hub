"""
Knowledge base connectors: Notion and Confluence Cloud, as credential
connectors (connectors/credentials.py) — a token the hub reaches out to,
with no inbound loop, the same shape connectors/microsoft and
connectors/trackers use. The clients that actually talk to the two APIs
live in connectors/knowledge/notion.py and connectors/knowledge/confluence.py;
this module only wires their stored config to the generic
/api/connectors/notion and /api/connectors/confluence routes, and to the
``test`` callback each one shows on the Connectors page.
"""
from __future__ import annotations

from typing import Any

from connectors.channels.registry import ConfigField
from connectors.channels.store import ChannelStore
from connectors.credentials import CredentialSpec

NOTION_STORE = ChannelStore("notion", secret_fields=("api_token",))
CONFLUENCE_STORE = ChannelStore("confluence", secret_fields=("api_token",))


def _notion_test() -> dict[str, Any]:
    from .notion import NotionClient, NotionError

    try:
        client = NotionClient(NOTION_STORE.get("api_token"))
        return client.test_connection()
    except NotionError as exc:
        return {"ok": False, "error": str(exc)}


def _confluence_test() -> dict[str, Any]:
    from .confluence import ConfluenceClient, ConfluenceError

    try:
        client = ConfluenceClient(
            CONFLUENCE_STORE.get("base_url"), CONFLUENCE_STORE.get("email"),
            CONFLUENCE_STORE.get("api_token"),
        )
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

__all__ = ["NOTION", "CONFLUENCE", "CREDENTIALS_LIST", "NOTION_STORE", "CONFLUENCE_STORE"]
