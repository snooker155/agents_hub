"""
tools/knowledge.py — Notion and Confluence Cloud as a workspace's knowledge
base: search, read a page as markdown, import it as a workspace file, or
write back (create a page, append to one).

Tool ids pre-assigned in tools/capabilities.py: ``notion_search``,
``notion_read_page``, ``notion_import`` (INGESTS_UNTRUSTED + READS_PRIVATE —
a page was written by whoever has access to the operator's Notion workspace,
not the agent), ``notion_create_page``, ``notion_append`` (CAN_EXFILTRATE —
agent-written text leaves the hub and lands in that workspace), and the
confluence_* mirrors of all five. Both connectors are wired through
connectors/knowledge (CredentialSpec, no inbound loop); the clients that
talk to the two APIs are connectors/knowledge/notion.py and
connectors/knowledge/confluence.py.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.web import wrap_untrusted

_NOTION_NOT_CONFIGURED = "Notion connector is not configured on the Connectors page"
_CONFLUENCE_NOT_CONFIGURED = "Confluence connector is not configured on the Connectors page"
_MAX_TEXT_CHARS = 60_000


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _truncate(text: str) -> Tuple[str, bool]:
    if len(text) <= _MAX_TEXT_CHARS:
        return text, False
    return text[:_MAX_TEXT_CHARS], True


def _notion_client():
    """A configured ``NotionClient``, or None when the connector has no token."""
    from connectors.knowledge import NOTION
    if not NOTION.is_configured():
        return None
    from connectors.knowledge.notion import NotionClient
    return NotionClient(NOTION.store.get("api_token"))


def _confluence_client():
    """A configured ``ConfluenceClient``, or None when the connector is bare.

    Can still raise ``ConfluenceError`` for a configured-but-refused base URL
    (the SSRF check on a non-atlassian.net host) — callers handle that the
    same way they handle any other ``ConfluenceError``.
    """
    from connectors.knowledge import CONFLUENCE
    if not CONFLUENCE.is_configured():
        return None
    from connectors.knowledge.confluence import ConfluenceClient
    store = CONFLUENCE.store
    return ConfluenceClient(store.get("base_url"), store.get("email"), store.get("api_token"))


def _import_file(source: str, title: str, page_id: str, url: str, markdown: str,
                 given_name: Optional[str]) -> Dict[str, Any]:
    from common.workspace_context import resolve_active_workspace
    from files.service import create_file

    workspace = resolve_active_workspace()
    if not workspace:
        raise ValueError("no active workspace to import into")
    file_name = (given_name or title or page_id).strip() or page_id
    if not file_name.lower().endswith(".md"):
        file_name = f"{file_name}.md"
    return create_file(
        workspace, file_name, markdown.encode("utf-8"), mime_type="text/markdown",
        source=source, meta={f"{source}_page_id": page_id, "url": url},
    )


# ── Notion ───────────────────────────────────────────────────────────────────

class NotionSearchInput(BaseModel):
    query: str = Field(..., description="Text to search for across the integration's shared pages")
    limit: int = Field(10, ge=1, le=50, description="Maximum number of pages to return")


@tool("notion_search", args_schema=NotionSearchInput)
def notion_search(query: str, limit: int = 10) -> str:
    """Search the pages the Notion integration has been shared with.

    Returns titles, ids and URLs, not page content; use notion_read_page for
    that. Titles are untrusted text, written by whoever has access to the
    operator's Notion workspace, not the agent.
    """
    client = _notion_client()
    if client is None:
        return _err(_NOTION_NOT_CONFIGURED)
    from connectors.knowledge.notion import NotionError
    try:
        results = client.search(query, limit=limit)
    except NotionError as exc:
        return _err(str(exc))
    return _ok({"results": results})


class NotionReadPageInput(BaseModel):
    page_id: str = Field(..., description="The Notion page id")


@tool("notion_read_page", args_schema=NotionReadPageInput)
def notion_read_page(page_id: str) -> str:
    """Read a Notion page's content as markdown.

    The page must be shared with the integration (the Connectors page shows
    how). Content is untrusted text, wrapped accordingly; pages past 60,000
    characters are truncated.
    """
    client = _notion_client()
    if client is None:
        return _err(_NOTION_NOT_CONFIGURED)
    from connectors.knowledge.notion import NotionError
    try:
        page = client.get_page(page_id)
    except NotionError as exc:
        return _err(str(exc))
    text, truncated = _truncate(page.get("text") or "")
    wrapped = wrap_untrusted(f"notion page {page.get('title') or page_id}", text)
    return _ok({"id": page.get("id"), "title": page.get("title"), "url": page.get("url"),
               "text": wrapped, "truncated": truncated})


class NotionImportInput(BaseModel):
    page_id: str = Field(..., description="The Notion page id to import")
    name: Optional[str] = Field(None, description="File name to save as; defaults to the page title")


@tool("notion_import", args_schema=NotionImportInput)
def notion_import(page_id: str, name: Optional[str] = None) -> str:
    """Import a Notion page into the active workspace's files, as markdown.

    The file is indexed for RAG and listed on the Artifacts page like any
    other workspace file.
    """
    client = _notion_client()
    if client is None:
        return _err(_NOTION_NOT_CONFIGURED)
    from connectors.knowledge.notion import NotionError
    try:
        page = client.get_page(page_id)
    except NotionError as exc:
        return _err(str(exc))
    try:
        record = _import_file("notion", page.get("title") or "", page_id, page.get("url") or "",
                              page.get("text") or "", name)
    except ValueError as exc:
        return _err(str(exc))
    return _ok({"file_id": record["file_id"], "name": record["name"], "workspace": record["workspace"]})


class NotionCreatePageInput(BaseModel):
    parent_page_id: str = Field(..., description="The Notion page id to create the new page under")
    title: str = Field(..., min_length=1, description="Title of the new page")
    markdown: str = Field(..., description="The page content as markdown")


@tool("notion_create_page", args_schema=NotionCreatePageInput)
def notion_create_page(parent_page_id: str, title: str, markdown: str) -> str:
    """Create a new Notion page under a parent page, from markdown.

    Sends agent-written content out to the operator's Notion workspace.
    """
    client = _notion_client()
    if client is None:
        return _err(_NOTION_NOT_CONFIGURED)
    from connectors.knowledge.notion import NotionError
    try:
        result = client.create_page(parent_page_id, title, markdown)
    except NotionError as exc:
        return _err(str(exc))
    return _ok(result)


class NotionAppendInput(BaseModel):
    page_id: str = Field(..., description="The Notion page id to append to")
    markdown: str = Field(..., description="Markdown content to append")


@tool("notion_append", args_schema=NotionAppendInput)
def notion_append(page_id: str, markdown: str) -> str:
    """Append markdown content to the end of an existing Notion page.

    Sends agent-written content out to the operator's Notion workspace.
    """
    client = _notion_client()
    if client is None:
        return _err(_NOTION_NOT_CONFIGURED)
    from connectors.knowledge.notion import NotionError
    try:
        result = client.append(page_id, markdown)
    except NotionError as exc:
        return _err(str(exc))
    return _ok(result)


# ── Confluence ───────────────────────────────────────────────────────────────

class ConfluenceSearchInput(BaseModel):
    query: str = Field(..., description="Plain text, or CQL (e.g. 'space = ENG and type = page')")
    limit: int = Field(10, ge=1, le=50, description="Maximum number of pages to return")


@tool("confluence_search", args_schema=ConfluenceSearchInput)
def confluence_search(query: str, limit: int = 10) -> str:
    """Search Confluence pages.

    A plain text query is wrapped into a CQL full text search restricted to
    pages; a query that already names a CQL field (type, space, text, title,
    label) is sent as is.
    """
    from connectors.knowledge.confluence import ConfluenceError
    try:
        client = _confluence_client()
    except ConfluenceError as exc:
        return _err(str(exc))
    if client is None:
        return _err(_CONFLUENCE_NOT_CONFIGURED)
    try:
        results = client.search(query, limit=limit)
    except ConfluenceError as exc:
        return _err(str(exc))
    return _ok({"results": results})


class ConfluenceReadPageInput(BaseModel):
    page_id: str = Field(..., description="The Confluence content id")


@tool("confluence_read_page", args_schema=ConfluenceReadPageInput)
def confluence_read_page(page_id: str) -> str:
    """Read a Confluence page's content as markdown, converted from its
    stored XHTML. Content is untrusted text, wrapped accordingly; pages past
    60,000 characters are truncated.
    """
    from connectors.knowledge.confluence import ConfluenceError
    try:
        client = _confluence_client()
    except ConfluenceError as exc:
        return _err(str(exc))
    if client is None:
        return _err(_CONFLUENCE_NOT_CONFIGURED)
    try:
        page = client.get_page(page_id)
    except ConfluenceError as exc:
        return _err(str(exc))
    text, truncated = _truncate(page.get("text") or "")
    wrapped = wrap_untrusted(f"confluence page {page.get('title') or page_id}", text)
    return _ok({"id": page.get("id"), "title": page.get("title"), "url": page.get("url"),
               "space": page.get("space"), "text": wrapped, "truncated": truncated})


class ConfluenceImportInput(BaseModel):
    page_id: str = Field(..., description="The Confluence content id to import")
    name: Optional[str] = Field(None, description="File name to save as; defaults to the page title")


@tool("confluence_import", args_schema=ConfluenceImportInput)
def confluence_import(page_id: str, name: Optional[str] = None) -> str:
    """Import a Confluence page into the active workspace's files, as
    markdown. The file is indexed for RAG and listed on the Artifacts page.
    """
    from connectors.knowledge.confluence import ConfluenceError
    try:
        client = _confluence_client()
    except ConfluenceError as exc:
        return _err(str(exc))
    if client is None:
        return _err(_CONFLUENCE_NOT_CONFIGURED)
    try:
        page = client.get_page(page_id)
    except ConfluenceError as exc:
        return _err(str(exc))
    try:
        record = _import_file("confluence", page.get("title") or "", page_id, page.get("url") or "",
                              page.get("text") or "", name)
    except ValueError as exc:
        return _err(str(exc))
    return _ok({"file_id": record["file_id"], "name": record["name"], "workspace": record["workspace"]})


class ConfluenceCreatePageInput(BaseModel):
    space_key: str = Field(..., description="The Confluence space key to create the page in")
    title: str = Field(..., min_length=1, description="Title of the new page")
    markdown: str = Field(..., description="The page content as markdown")
    parent_id: Optional[str] = Field(None, description="Parent page id, to create this as a child page")


@tool("confluence_create_page", args_schema=ConfluenceCreatePageInput)
def confluence_create_page(space_key: str, title: str, markdown: str,
                           parent_id: Optional[str] = None) -> str:
    """Create a new Confluence page from markdown.

    Sends agent-written content out to the operator's Confluence space.
    """
    from connectors.knowledge.confluence import ConfluenceError
    try:
        client = _confluence_client()
    except ConfluenceError as exc:
        return _err(str(exc))
    if client is None:
        return _err(_CONFLUENCE_NOT_CONFIGURED)
    try:
        result = client.create_page(space_key, title, markdown, parent_id=parent_id)
    except ConfluenceError as exc:
        return _err(str(exc))
    return _ok(result)


CONNECTOR_TOOLS = [
    notion_search, notion_read_page, notion_import, notion_create_page, notion_append,
    confluence_search, confluence_read_page, confluence_import, confluence_create_page,
]

__all__ = [
    "notion_search", "notion_read_page", "notion_import", "notion_create_page", "notion_append",
    "confluence_search", "confluence_read_page", "confluence_import", "confluence_create_page",
    "CONNECTOR_TOOLS",
]
