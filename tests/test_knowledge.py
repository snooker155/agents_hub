"""Notion and Confluence connectors (connectors/knowledge) and their tools
(tools/knowledge.py).

No network: every Notion and Confluence call goes through
``httpx.request``, monkeypatched per test with a small router keyed on
(method, url) that returns canned JSON and records what was sent.

Run: ``python -m pytest tests/test_knowledge.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _Resp:
    def __init__(self, data: Any) -> None:
        self.status_code = 200
        self._data = data

    def json(self) -> Any:
        return self._data


def _router(routes: dict, calls: Optional[list] = None):
    """``routes`` maps (method, url) -> the JSON body to answer with."""

    def fake_request(method, url, *, headers=None, json=None, params=None, timeout=None):  # noqa: A002
        if calls is not None:
            calls.append({"method": method, "url": url, "json": json, "params": params})
        key = (method, url)
        if key not in routes:
            raise AssertionError(f"unexpected request: {method} {url}")
        return _Resp(routes[key])

    return fake_request


def _configure_notion(token: str = "ntn_test123"):
    from connectors.knowledge import NOTION
    NOTION.store.set_config({"api_token": token})
    return NOTION


def _configure_confluence(base_url: str = "https://acme.atlassian.net/wiki",
                          email: str = "bot@acme.com", api_token: str = "tok-1"):
    from connectors.knowledge import CONFLUENCE
    CONFLUENCE.store.set_config({"base_url": base_url, "email": email, "api_token": api_token})
    return CONFLUENCE


# ── Notion: block tree -> markdown ──────────────────────────────────────────

def test_notion_block_tree_to_markdown_covers_nesting_code_table_and_rich_text(monkeypatch):
    _configure_notion()
    from connectors.knowledge import notion

    page = {
        "id": "page1", "url": "https://notion.so/page1",
        "properties": {"title": {"type": "title", "title": [
            {"type": "text", "plain_text": "My Page", "annotations": {}, "href": None}]}},
    }
    top_blocks = {
        "results": [
            {"object": "block", "id": "b1", "type": "bulleted_list_item", "has_children": True,
             "bulleted_list_item": {"rich_text": [
                 {"type": "text", "plain_text": "Parent item", "annotations": {}, "href": None}]}},
            {"object": "block", "id": "b2", "type": "code", "has_children": False,
             "code": {"rich_text": [
                 {"type": "text", "plain_text": "print('hi')", "annotations": {}, "href": None}],
                 "language": "python"}},
            {"object": "block", "id": "b3", "type": "table", "has_children": True,
             "table": {"has_column_header": True, "table_width": 2}},
            {"object": "block", "id": "b4", "type": "paragraph", "has_children": False,
             "paragraph": {"rich_text": [
                 {"type": "text", "plain_text": "Bold and ",
                  "annotations": {"bold": True}, "href": None},
                 {"type": "text", "plain_text": "link",
                  "annotations": {}, "href": "https://example.com"},
             ]}},
        ],
        "has_more": False,
    }
    nested_blocks = {"results": [
        {"object": "block", "id": "b1a", "type": "bulleted_list_item", "has_children": False,
         "bulleted_list_item": {"rich_text": [
             {"type": "text", "plain_text": "Nested item", "annotations": {}, "href": None}]}},
    ], "has_more": False}
    table_rows = {"results": [
        {"object": "block", "id": "r1", "type": "table_row", "has_children": False,
         "table_row": {"cells": [
             [{"type": "text", "plain_text": "H1", "annotations": {}, "href": None}],
             [{"type": "text", "plain_text": "H2", "annotations": {}, "href": None}]]}},
        {"object": "block", "id": "r2", "type": "table_row", "has_children": False,
         "table_row": {"cells": [
             [{"type": "text", "plain_text": "a", "annotations": {}, "href": None}],
             [{"type": "text", "plain_text": "b", "annotations": {}, "href": None}]]}},
    ], "has_more": False}

    routes = {
        ("GET", "https://api.notion.com/v1/pages/page1"): page,
        ("GET", "https://api.notion.com/v1/blocks/page1/children"): top_blocks,
        ("GET", "https://api.notion.com/v1/blocks/b1/children"): nested_blocks,
        ("GET", "https://api.notion.com/v1/blocks/b3/children"): table_rows,
    }
    monkeypatch.setattr(notion.httpx, "request", _router(routes))

    client = notion.NotionClient("ntn_test123")
    result = client.get_page("page1")
    assert result["id"] == "page1"
    assert result["title"] == "My Page"
    text = result["text"]
    assert "- Parent item" in text
    assert "  - Nested item" in text  # nested one level in
    assert "```python\nprint('hi')\n```" in text
    assert "| H1 | H2 |" in text and "| --- | --- |" in text and "| a | b |" in text
    assert "**Bold and **[link](https://example.com)" in text


# ── Notion: markdown -> blocks round trip ───────────────────────────────────

def test_notion_markdown_to_blocks_round_trip():
    from connectors.knowledge import notion

    md = (
        "# Title\n\n"
        "Some text with **bold** and a [link](https://example.com).\n\n"
        "- item one\n"
        "- item two\n\n"
        "1. first\n"
        "2. second\n\n"
        "```python\nprint('hi')\n```\n"
    )
    blocks = notion.markdown_to_blocks(md)
    types = [b["type"] for b in blocks]
    assert types == [
        "heading_1", "paragraph", "bulleted_list_item", "bulleted_list_item",
        "numbered_list_item", "numbered_list_item", "code",
    ]

    heading_text = blocks[0]["heading_1"]["rich_text"][0]["text"]["content"]
    assert heading_text == "Title"

    para_segments = blocks[1]["paragraph"]["rich_text"]
    bold_seg = next(s for s in para_segments if s["annotations"]["bold"])
    assert bold_seg["text"]["content"] == "bold"
    link_seg = next(s for s in para_segments if s.get("href"))
    assert link_seg["href"] == "https://example.com"
    assert link_seg["text"]["content"] == "link"

    assert blocks[2]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "item one"
    assert blocks[4]["numbered_list_item"]["rich_text"][0]["text"]["content"] == "first"
    assert blocks[6]["code"]["language"] == "python"
    assert blocks[6]["code"]["rich_text"][0]["text"]["content"] == "print('hi')"

    # Render each block back out and check the markdown shapes survive.
    rendered = [notion._render_block(b, b["type"], "", 1) for b in blocks]
    assert rendered[0] == "# Title"
    assert "**bold**" in rendered[1] and "[link](https://example.com)" in rendered[1]
    assert rendered[2] == "- item one"
    assert rendered[4] == "1. first"
    assert rendered[6] == "```python\nprint('hi')\n```"


# ── Notion: search filters to pages ─────────────────────────────────────────

def test_notion_search_filters_to_pages(monkeypatch):
    from connectors.knowledge import notion

    data = {"results": [
        {"object": "page", "id": "p1", "url": "https://notion.so/p1", "last_edited_time": "2026-01-01",
         "properties": {"title": {"type": "title", "title": [
             {"type": "text", "plain_text": "A page", "annotations": {}, "href": None}]}}},
        {"object": "database", "id": "d1"},
    ]}
    monkeypatch.setattr(notion.httpx, "request",
                        _router({("POST", "https://api.notion.com/v1/search"): data}))
    client = notion.NotionClient("tok")
    results = client.search("hello", limit=10)
    assert len(results) == 1
    assert results[0]["id"] == "p1"
    assert results[0]["title"] == "A page"
    assert results[0]["object"] == "page"


def test_notion_search_tool_reports_unconfigured():
    _configure_notion(token="")
    from tools.knowledge import notion_search
    out = json.loads(notion_search.invoke({"query": "hello"}))
    assert out["ok"] is False
    assert "not configured" in out["error"]


# ── Notion: import writes a workspace file ──────────────────────────────────

def test_notion_import_writes_a_workspace_file(monkeypatch):
    _configure_notion()
    from connectors.knowledge import notion
    from common.workspace_context import _workspace_ctx

    page = {
        "id": "page9", "url": "https://notion.so/page9",
        "properties": {"title": {"type": "title", "title": [
            {"type": "text", "plain_text": "Imported Page", "annotations": {}, "href": None}]}},
    }
    blocks = {"results": [
        {"object": "block", "id": "x1", "type": "paragraph", "has_children": False,
         "paragraph": {"rich_text": [
             {"type": "text", "plain_text": "Body text", "annotations": {}, "href": None}]}},
    ], "has_more": False}
    routes = {
        ("GET", "https://api.notion.com/v1/pages/page9"): page,
        ("GET", "https://api.notion.com/v1/blocks/page9/children"): blocks,
    }
    monkeypatch.setattr(notion.httpx, "request", _router(routes))

    from tools.knowledge import notion_import
    from files import service

    token = _workspace_ctx.set("acme")
    try:
        out = json.loads(notion_import.invoke({"page_id": "page9"}))
    finally:
        _workspace_ctx.reset(token)

    assert out["ok"] is True
    assert out["workspace"] == "acme"
    record = service.get_file(out["file_id"])
    assert record is not None
    assert record["name"] == "Imported Page.md"
    assert record["mime_type"] == "text/markdown"
    assert record["source"] == "notion"
    assert record["meta"]["notion_page_id"] == "page9"
    assert record["meta"]["url"] == "https://notion.so/page9"
    assert "Body text" in service.read_bytes(out["file_id"]).decode("utf-8")


# ── Confluence: storage XHTML -> markdown ───────────────────────────────────

def test_confluence_storage_to_markdown_covers_heading_list_code_table_link():
    from connectors.knowledge import confluence

    xhtml = (
        "<h2>Heading</h2>"
        "<p>A paragraph with a <a href=\"https://example.com\">link</a>.</p>"
        "<ul><li>item one</li><li>item two</li></ul>"
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        "<ac:plain-text-body><![CDATA[print('hi')]]></ac:plain-text-body>"
        "</ac:structured-macro>"
        "<table><tbody><tr><td>H1</td><td>H2</td></tr><tr><td>a</td><td>b</td></tr></tbody></table>"
    )
    text = confluence.storage_to_markdown(xhtml)
    assert "## Heading" in text
    assert "[link](https://example.com)" in text
    assert "- item one" in text and "- item two" in text
    assert "```python\nprint('hi')\n```" in text
    assert "| H1 | H2 |" in text and "| a | b |" in text


# ── Confluence: markdown -> storage ──────────────────────────────────────────

def test_confluence_markdown_to_storage():
    from connectors.knowledge import confluence

    md = (
        "## Heading\n\n"
        "A paragraph with **bold** and a [link](https://example.com).\n\n"
        "- item one\n"
        "- item two\n\n"
        "```python\nprint('hi')\n```\n"
    )
    storage = confluence.markdown_to_storage(md)
    assert "<h2>Heading</h2>" in storage
    assert "<strong>bold</strong>" in storage
    assert '<a href="https://example.com">link</a>' in storage
    assert "<ul><li>item one</li><li>item two</li></ul>" in storage
    assert '<ac:structured-macro ac:name="code">' in storage
    assert "<![CDATA[print('hi')]]>" in storage

    # Round trip: converting the storage back gives the same shapes again.
    back = confluence.storage_to_markdown(storage)
    assert "## Heading" in back
    assert "[link](https://example.com)" in back
    assert "- item one" in back
    assert "```python\nprint('hi')\n```" in back


# ── Confluence: a plain text query becomes CQL ──────────────────────────────

def test_confluence_plain_text_query_becomes_cql():
    from connectors.knowledge.confluence import _as_cql
    assert _as_cql("hello world") == 'text ~ "hello world" and type = page'
    assert _as_cql('with "quotes"') == 'text ~ "with \\"quotes\\"" and type = page'
    # Already-CQL input is used as is.
    assert _as_cql("space = ENG and type = page") == "space = ENG and type = page"


def test_confluence_search_sends_the_built_cql(monkeypatch):
    _configure_confluence()
    from connectors.knowledge import confluence

    calls: list = []
    data = {"results": [
        {"id": "c1", "title": "A page", "space": {"key": "ENG"},
         "version": {"when": "2026-01-01T00:00:00.000Z"},
         "_links": {"webui": "/spaces/ENG/pages/c1/A+page"}},
    ]}
    monkeypatch.setattr(
        confluence.httpx, "request",
        _router({("GET", "https://acme.atlassian.net/wiki/rest/api/content/search"): data}, calls))

    client = confluence.ConfluenceClient("https://acme.atlassian.net/wiki", "bot@acme.com", "tok-1")
    results = client.search("roadmap", limit=5)
    assert results[0]["id"] == "c1"
    assert results[0]["space"] == "ENG"
    assert results[0]["url"] == "https://acme.atlassian.net/wiki/spaces/ENG/pages/c1/A+page"
    assert calls[0]["params"]["cql"] == 'text ~ "roadmap" and type = page'


# ── Confluence: test identity ───────────────────────────────────────────────

def test_confluence_test_connection_identity(monkeypatch):
    _configure_confluence()
    from connectors.knowledge import CONFLUENCE, confluence

    monkeypatch.setattr(
        confluence.httpx, "request",
        _router({("GET", "https://acme.atlassian.net/wiki/rest/api/user/current"):
                 {"displayName": "Anna Admin", "username": "anna"}}))
    result = CONFLUENCE.test()
    assert result["ok"] is True
    assert result["identity"] == "Anna Admin"


def test_confluence_search_tool_reports_unconfigured():
    from connectors.knowledge import CONFLUENCE
    CONFLUENCE.store.set_config({"base_url": "", "email": "", "api_token": ""},
                                clear=["api_token"])
    from tools.knowledge import confluence_search
    out = json.loads(confluence_search.invoke({"query": "hello"}))
    assert out["ok"] is False
    assert "not configured" in out["error"]


# ── public_config masks tokens ──────────────────────────────────────────────

def test_public_config_masks_tokens():
    _configure_notion(token="ntn_secret")
    _configure_confluence(api_token="super-secret")
    from connectors.knowledge import NOTION, CONFLUENCE

    notion_public = NOTION.store.public_config()
    assert notion_public.get("has_api_token") is True
    assert "api_token" not in notion_public
    assert "ntn_secret" not in json.dumps(notion_public)

    confluence_public = CONFLUENCE.store.public_config()
    assert confluence_public.get("has_api_token") is True
    assert "api_token" not in confluence_public
    assert confluence_public.get("base_url") == "https://acme.atlassian.net/wiki"
    assert "super-secret" not in json.dumps(confluence_public)
