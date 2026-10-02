"""
connectors/knowledge/notion.py — a thin client for the Notion API.

Search the pages an internal integration has been shared with, read one as
markdown (walking its block children recursively, the only way Notion's API
hands back a page's body), and write: create a page or append to one from
markdown. Used by this package's own ``test`` callback
(connectors/knowledge/__init__.py) and by tools/knowledge.py's notion_* tools.

The markdown <-> block conversion is deliberately small: it covers the block
types a knowledge base realistically produces (paragraphs, headings, lists,
to-dos, quotes, code, callouts, toggles, dividers, tables, links to child
pages; bold, italic, code and links in rich text) rather than the whole
Notion block catalog. Reading is capped at a recursion depth of 4 and 2000
blocks total, so a pathologically large or cyclical page cannot hang a tool
call.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx

_API_BASE = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TIMEOUT = 30.0
_PAGE_SIZE = 100
_MAX_DEPTH = 4
_MAX_BLOCKS = 2000


class NotionError(Exception):
    """A UI-safe error from a Notion API call."""


class NotionClient:
    def __init__(self, api_token: str) -> None:
        self.token = (api_token or "").strip()
        if not self.token:
            raise NotionError("No Notion token configured. Add one on the Connectors page.")

    # ── transport ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, json: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict[str, Any]:
        url = f"{_API_BASE}{path}"
        try:
            resp = httpx.request(method, url, headers=self._headers(), json=json,
                                 params=params, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise NotionError(f"Notion API request failed: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            raise NotionError("Notion token is invalid or expired")
        if resp.status_code == 404:
            raise NotionError("Notion page not found, or not shared with the integration")
        if resp.status_code == 429:
            raise NotionError("Notion API rate limited; try again shortly")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str((resp.json() or {}).get("message") or "")
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise NotionError(f"Notion API error {resp.status_code}{suffix}")
        try:
            return resp.json()
        except Exception as exc:
            raise NotionError("Notion API returned an unreadable response") from exc

    # ── read ─────────────────────────────────────────────────────────────

    def test_connection(self) -> dict[str, Any]:
        data = self._request("GET", "/users/me")
        name = (data.get("name")
                or ((data.get("bot") or {}).get("owner") or {}).get("workspace_name")
                or "Notion")
        return {"ok": True, "identity": name}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 10), 100))
        body = {
            "query": query or "",
            "filter": {"property": "object", "value": "page"},
            "page_size": limit,
        }
        data = self._request("POST", "/search", json=body)
        out: list[dict[str, Any]] = []
        for item in (data.get("results") or []):
            if item.get("object") != "page":
                continue
            out.append({
                "id": item.get("id"),
                "title": _page_title(item),
                "url": item.get("url"),
                "last_edited": item.get("last_edited_time"),
                "object": item.get("object"),
            })
            if len(out) >= limit:
                break
        return out

    def _list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"page_size": _PAGE_SIZE}
            if cursor:
                params["start_cursor"] = cursor
            data = self._request("GET", f"/blocks/{block_id}/children", params=params)
            out.extend(data.get("results") or [])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return out

    def get_page(self, page_id: str) -> dict[str, Any]:
        page = self._request("GET", f"/pages/{page_id}")
        counter = {"n": 0}
        lines = self._blocks_to_markdown(page_id, depth=0, counter=counter)
        return {
            "id": page.get("id"), "title": _page_title(page), "url": page.get("url"),
            "text": "\n\n".join(lines),
        }

    def _blocks_to_markdown(self, block_id: str, *, depth: int, counter: dict[str, int]) -> list[str]:
        lines: list[str] = []
        if depth > _MAX_DEPTH:
            return lines
        children = self._list_block_children(block_id)
        number = 0
        prev_type: Optional[str] = None
        for block in children:
            if counter["n"] >= _MAX_BLOCKS:
                break
            counter["n"] += 1
            btype = block.get("type") or ""
            indent = "  " * depth
            number = (number + 1) if btype == "numbered_list_item" and prev_type == btype else (
                1 if btype == "numbered_list_item" else 0)
            if btype == "table":
                lines.append(self._table_to_markdown(block))
                prev_type = btype
                continue
            line = _render_block(block, btype, indent, number)
            if line is not None:
                lines.append(line)
            if block.get("has_children") and btype not in ("table", "child_page"):
                lines.extend(self._blocks_to_markdown(block["id"], depth=depth + 1, counter=counter))
            prev_type = btype
        return lines

    def _table_to_markdown(self, table_block: dict[str, Any]) -> str:
        rows = self._list_block_children(table_block["id"])
        has_header = bool((table_block.get("table") or {}).get("has_column_header"))
        out_lines: list[str] = []
        for i, row in enumerate(rows):
            cells = (row.get("table_row") or {}).get("cells") or []
            cell_text = [_rich_text_to_md(c).replace("|", "\\|") for c in cells]
            out_lines.append("| " + " | ".join(cell_text) + " |")
            if i == 0 and has_header:
                out_lines.append("| " + " | ".join("---" for _ in cell_text) + " |")
        return "\n".join(out_lines)

    # ── write ────────────────────────────────────────────────────────────

    def create_page(self, parent_page_id: str, title: str, markdown: str) -> dict[str, Any]:
        blocks = markdown_to_blocks(markdown)
        first, rest = blocks[:100], blocks[100:]
        body = {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title or ""}}]}},
            "children": first,
        }
        data = self._request("POST", "/pages", json=body)
        page_id = data.get("id")
        for i in range(0, len(rest), 100):
            self._request("PATCH", f"/blocks/{page_id}/children", json={"children": rest[i:i + 100]})
        return {"id": page_id, "url": data.get("url")}

    def append(self, page_id: str, markdown: str) -> dict[str, Any]:
        blocks = markdown_to_blocks(markdown)
        for i in range(0, len(blocks), 100):
            self._request("PATCH", f"/blocks/{page_id}/children", json={"children": blocks[i:i + 100]})
        return {"id": page_id, "appended": len(blocks)}


# ── page/rich text helpers ──────────────────────────────────────────────────

def _page_title(page: dict[str, Any]) -> str:
    props = page.get("properties") or {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _rich_text_to_md(prop.get("title") or [])
    child = page.get("child_page")
    if isinstance(child, dict) and child.get("title"):
        return str(child["title"])
    return ""


def _rich_text_to_md(rich_text: Optional[list[dict[str, Any]]]) -> str:
    parts: list[str] = []
    for rt in rich_text or []:
        text = rt.get("plain_text")
        if text is None:
            text = (rt.get("text") or {}).get("content", "")
        ann = rt.get("annotations") or {}
        seg = text
        if ann.get("code"):
            seg = f"`{seg}`"
        if ann.get("bold"):
            seg = f"**{seg}**"
        if ann.get("italic"):
            seg = f"*{seg}*"
        link = (rt.get("text") or {}).get("link") if isinstance(rt.get("text"), dict) else None
        href = rt.get("href") or (link or {}).get("url")
        if href:
            seg = f"[{seg}]({href})"
        parts.append(seg)
    return "".join(parts)


def _render_block(block: dict[str, Any], btype: str, indent: str, number: int) -> Optional[str]:
    data = block.get(btype) or {}
    rich = data.get("rich_text")
    text = _rich_text_to_md(rich) if rich is not None else ""
    if btype == "paragraph":
        return f"{indent}{text}" if text else None
    if btype == "heading_1":
        return f"{indent}# {text}"
    if btype == "heading_2":
        return f"{indent}## {text}"
    if btype == "heading_3":
        return f"{indent}### {text}"
    if btype == "bulleted_list_item":
        return f"{indent}- {text}"
    if btype == "numbered_list_item":
        return f"{indent}{number}. {text}"
    if btype == "to_do":
        mark = "x" if data.get("checked") else " "
        return f"{indent}- [{mark}] {text}"
    if btype == "quote":
        return f"{indent}> {text}"
    if btype == "code":
        language = data.get("language") or ""
        return f"{indent}```{language}\n{text}\n{indent}```"
    if btype == "callout":
        icon = (data.get("icon") or {}).get("emoji") or ""
        prefix = f"{icon} " if icon else ""
        return f"{indent}> {prefix}{text}"
    if btype == "toggle":
        return f"{indent}**{text}**"
    if btype == "divider":
        return f"{indent}---"
    if btype == "child_page":
        title = (block.get("child_page") or {}).get("title") or "Untitled"
        page_id = (block.get("id") or "").replace("-", "")
        return f"{indent}[{title}](https://www.notion.so/{page_id})"
    return f"{indent}{text}" if text else None


# ── markdown -> blocks ───────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)|(?P<bold>\*\*[^*]+\*\*)|(?P<link>\[[^\]]+\]\([^)]+\))|(?P<italic>\*[^*]+\*|_[^_]+_)"
)


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """A small set of Notion blocks for ``markdown``: headings (#, ##, ###),
    bullets (``- ``), numbers (``1. ``), fenced code, and blank-line
    separated paragraphs for everything else."""
    lines = (markdown or "").splitlines()
    blocks: list[dict[str, Any]] = []
    i = 0
    paragraph_buf: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buf:
            text = "\n".join(paragraph_buf).strip()
            if text:
                blocks.append(_paragraph_block(text))
            paragraph_buf.clear()

    while i < len(lines):
        stripped = lines[i].strip()
        fence = _FENCE_RE.match(stripped)
        if fence:
            flush_paragraph()
            language = fence.group(1) or "plain text"
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # the closing fence
            blocks.append(_code_block("\n".join(code_lines), language))
            continue
        if not stripped:
            flush_paragraph()
            i += 1
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            blocks.append(_heading_block(len(heading.group(1)), heading.group(2)))
            i += 1
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            flush_paragraph()
            blocks.append(_list_item_block("bulleted_list_item", bullet.group(1)))
            i += 1
            continue
        number = _NUMBER_RE.match(stripped)
        if number:
            flush_paragraph()
            blocks.append(_list_item_block("numbered_list_item", number.group(1)))
            i += 1
            continue
        paragraph_buf.append(stripped)
        i += 1
    flush_paragraph()
    return blocks


def _heading_block(level: int, text: str) -> dict[str, Any]:
    kind = {1: "heading_1", 2: "heading_2", 3: "heading_3"}[min(max(level, 1), 3)]
    return {"object": "block", "type": kind, kind: {"rich_text": _md_to_rich_text(text)}}


def _list_item_block(kind: str, text: str) -> dict[str, Any]:
    return {"object": "block", "type": kind, kind: {"rich_text": _md_to_rich_text(text)}}


def _paragraph_block(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _md_to_rich_text(text)}}


def _code_block(content: str, language: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "code",
        "code": {"rich_text": [{"type": "text", "text": {"content": content}}],
                 "language": language or "plain text"},
    }


def _md_to_rich_text(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append(_text_segment(text[pos:m.start()]))
        if m.lastgroup == "code":
            out.append(_text_segment(m.group("code")[1:-1], code=True))
        elif m.lastgroup == "bold":
            out.append(_text_segment(m.group("bold")[2:-2], bold=True))
        elif m.lastgroup == "italic":
            seg = m.group("italic")
            out.append(_text_segment(seg[1:-1], italic=True))
        elif m.lastgroup == "link":
            seg = m.group("link")
            label, _, rest = seg[1:].partition("](")
            out.append(_text_segment(label, href=rest[:-1]))
        pos = m.end()
    if pos < len(text) or not out:
        out.append(_text_segment(text[pos:]))
    return out


def _text_segment(content: str, *, bold: bool = False, italic: bool = False, code: bool = False,
                   href: Optional[str] = None) -> dict[str, Any]:
    seg: dict[str, Any] = {
        "type": "text",
        "text": {"content": content, "link": ({"url": href} if href else None)},
        "annotations": {"bold": bold, "italic": italic, "strikethrough": False,
                        "underline": False, "code": code, "color": "default"},
    }
    if href:
        seg["href"] = href
    return seg


__all__ = ["NotionClient", "NotionError", "markdown_to_blocks"]
