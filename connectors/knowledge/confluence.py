"""
connectors/knowledge/confluence.py — a thin client for the Confluence Cloud
REST API (v1, ``/rest/api``, which works on Cloud and Server alike).

Search, read a page as markdown (the stored XHTML format walked with the
stdlib's ``html.parser``, so no extra dependency), and write: create a page
from markdown. Used by this package's own ``test`` callback
(connectors/knowledge/__init__.py) and by tools/knowledge.py's confluence_*
tools.

A self-hosted base URL is the operator's own choice, same as
connectors/git's Gitea base URL — except Confluence Cloud's own domain
(``*.atlassian.net``) is what most operators actually type, so only a base
URL that is *not* on that domain goes through ``common.ssrf.resolve_and_check``
before any request is made.
"""
from __future__ import annotations

import base64
import html
import re
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from common.ssrf import resolve_and_check

_TIMEOUT = 30.0


class ConfluenceError(Exception):
    """A UI-safe error from a Confluence API call."""


def _is_atlassian_host(host: str) -> bool:
    host = (host or "").lower()
    return host == "atlassian.net" or host.endswith(".atlassian.net")


class ConfluenceClient:
    def __init__(self, base_url: str, email: str, api_token: str) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.email = (email or "").strip()
        self.token = (api_token or "").strip()
        if not (self.base_url and self.email and self.token):
            raise ConfluenceError(
                "No Confluence connection configured. Add a base URL, email and "
                "API token on the Connectors page."
            )
        host = urlparse(self.base_url).hostname or ""
        if not _is_atlassian_host(host):
            ok, reason = resolve_and_check(host)
            if not ok:
                raise ConfluenceError(f"Confluence base URL refused: {reason}")
        self._api = f"{self.base_url}/rest/api"

    # ── transport ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        basic = base64.b64encode(f"{self.email}:{self.token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {basic}", "Content-Type": "application/json",
                "Accept": "application/json"}

    def _request(self, method: str, path: str, *, json: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict[str, Any]:
        url = f"{self._api}{path}"
        try:
            resp = httpx.request(method, url, headers=self._headers(), json=json,
                                 params=params, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise ConfluenceError(f"Confluence API request failed: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            raise ConfluenceError("Confluence credentials are invalid or expired")
        if resp.status_code == 404:
            raise ConfluenceError("Confluence page not found")
        if resp.status_code == 429:
            raise ConfluenceError("Confluence API rate limited; try again shortly")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str((resp.json() or {}).get("message") or "")
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise ConfluenceError(f"Confluence API error {resp.status_code}{suffix}")
        try:
            return resp.json()
        except Exception as exc:
            raise ConfluenceError("Confluence API returned an unreadable response") from exc

    # ── read ─────────────────────────────────────────────────────────────

    def test_connection(self) -> dict[str, Any]:
        data = self._request("GET", "/user/current")
        return {"ok": True, "identity": data.get("displayName") or data.get("username") or "Confluence"}

    def search(self, cql_or_text: str, limit: int = 10) -> list[dict[str, Any]]:
        cql = _as_cql(cql_or_text)
        data = self._request("GET", "/content/search", params={
            "cql": cql, "limit": max(1, min(int(limit or 10), 100)), "expand": "space,version",
        })
        out: list[dict[str, Any]] = []
        for item in (data.get("results") or []):
            links = item.get("_links") or {}
            out.append({
                "id": item.get("id"),
                "title": item.get("title") or "",
                "url": self.base_url + (links.get("webui") or ""),
                "space": (item.get("space") or {}).get("key") or "",
                "last_edited": ((item.get("version") or {}).get("when")) or "",
            })
        return out

    def get_page(self, page_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/content/{page_id}", params={"expand": "body.storage,space,version"})
        storage = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
        links = data.get("_links") or {}
        return {
            "id": data.get("id"), "title": data.get("title") or "",
            "url": self.base_url + (links.get("webui") or ""),
            "space": (data.get("space") or {}).get("key") or "",
            "text": storage_to_markdown(storage),
        }

    # ── write ────────────────────────────────────────────────────────────

    def create_page(self, space_key: str, title: str, markdown: str,
                    parent_id: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": markdown_to_storage(markdown), "representation": "storage"}},
        }
        if parent_id:
            body["ancestors"] = [{"id": parent_id}]
        data = self._request("POST", "/content", json=body)
        links = data.get("_links") or {}
        return {"id": data.get("id"), "url": self.base_url + (links.get("webui") or "")}


def _as_cql(query: str) -> str:
    """``query`` as is when it already names a CQL field, else a full text
    search over pages."""
    q = (query or "").strip()
    if not q:
        return "type = page"
    if re.search(r"\b(type|space|text|title|label)\s*[=~]", q, re.I):
        return q
    escaped = q.replace("\\", "\\\\").replace('"', '\\"')
    return f'text ~ "{escaped}" and type = page'


# ── storage (XHTML) -> markdown ─────────────────────────────────────────────

_CODE_MACRO_RE = re.compile(
    r'<ac:structured-macro\s+ac:name="code"[^>]*>(?P<body>.*?)</ac:structured-macro>', re.S)
_LANG_RE = re.compile(r'<ac:parameter\s+ac:name="language"[^>]*>(?P<lang>.*?)</ac:parameter>', re.S)
_CDATA_RE = re.compile(
    r'<ac:plain-text-body>\s*<!\[CDATA\[(?P<code>.*?)\]\]>\s*</ac:plain-text-body>', re.S)


class _StorageParser(HTMLParser):
    """Walks Confluence's storage XHTML into a flat list of markdown blocks.
    Code macros are substituted out before this runs (see
    :func:`storage_to_markdown`), so this only ever sees plain XHTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._buf: list[str] = []
        self._list_stack: list[dict[str, Any]] = []
        self._link_href: Optional[str] = None
        self._in_cell = False
        self._row: list[str] = []
        self._table_rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in ("ul", "ol"):
            self._list_stack.append({"kind": tag, "n": 0})
        elif tag in ("strong", "b"):
            self._buf.append("**")
        elif tag in ("em", "i"):
            self._buf.append("*")
        elif tag == "code":
            self._buf.append("`")
        elif tag == "a":
            self._link_href = dict(attrs).get("href") or ""
            self._buf.append("[")
        elif tag == "br":
            self._buf.append("\n")
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = "".join(self._buf).strip()
            self._buf = []
            if self._in_cell:
                self._buf.append(text)
            elif text:
                self._out.append(f"{'#' * int(tag[1])} {text}")
        elif tag == "p":
            text = "".join(self._buf).strip()
            self._buf = []
            if self._in_cell:
                self._buf.append(text)
            elif text:
                self._out.append(text)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
        elif tag == "li":
            text = "".join(self._buf).strip()
            self._buf = []
            if self._list_stack:
                top = self._list_stack[-1]
                if top["kind"] == "ol":
                    top["n"] += 1
                    marker = f"{top['n']}. "
                else:
                    marker = "- "
            else:
                marker = "- "
            indent = "  " * max(0, len(self._list_stack) - 1)
            self._out.append(f"{indent}{marker}{text}")
        elif tag in ("strong", "b"):
            self._buf.append("**")
        elif tag in ("em", "i"):
            self._buf.append("*")
        elif tag == "code":
            self._buf.append("`")
        elif tag == "a":
            self._buf.append(f"]({self._link_href or ''})")
            self._link_href = None
        elif tag in ("td", "th"):
            text = "".join(self._buf).strip()
            self._buf = []
            self._row.append(text)
            self._in_cell = False
        elif tag == "tr":
            if self._row:
                self._table_rows.append(self._row)
            self._row = []
        elif tag == "table":
            self._emit_table()

    def handle_data(self, data: str) -> None:
        self._buf.append(data)

    def _emit_table(self) -> None:
        rows, self._table_rows = self._table_rows, []
        if not rows:
            return
        for i, row in enumerate(rows):
            cells = [c.replace("|", "\\|") for c in row]
            self._out.append("| " + " | ".join(cells) + " |")
            if i == 0:
                self._out.append("| " + " | ".join("---" for _ in cells) + " |")

    def result(self) -> str:
        tail = "".join(self._buf).strip()
        if tail:
            self._out.append(tail)
        return "\n\n".join(self._out)


def storage_to_markdown(xhtml: str) -> str:
    """Confluence's storage format (XHTML with ``ac:`` macros) as markdown:
    headings, paragraphs, lists, a code macro as fenced code, tables as pipe
    rows, links as ``[text](href)``; everything else stripped."""
    xhtml = xhtml or ""
    code_blocks: list[str] = []

    def _extract_code(match: "re.Match[str]") -> str:
        body = match.group("body")
        lang_m = _LANG_RE.search(body)
        code_m = _CDATA_RE.search(body)
        language = (lang_m.group("lang") if lang_m else "") or ""
        code = html.unescape(code_m.group("code") if code_m else "")
        placeholder = f"\x00CODEBLOCK{len(code_blocks)}\x00"
        code_blocks.append(f"```{language}\n{code}\n```")
        return f"<p>{placeholder}</p>"

    xhtml = _CODE_MACRO_RE.sub(_extract_code, xhtml)
    parser = _StorageParser()
    parser.feed(xhtml)
    parser.close()
    text = parser.result()
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{i}\x00", block)
    return text


# ── markdown -> storage (XHTML) ─────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_to_storage(text: str) -> str:
    text = _escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def markdown_to_storage(markdown: str) -> str:
    """Markdown as Confluence storage-format XHTML: headings, paragraphs,
    lists, fenced code as the code macro; text is escaped."""
    lines = (markdown or "").splitlines()
    out: list[str] = []
    i = 0
    paragraph_buf: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buf:
            text = _inline_to_storage(" ".join(paragraph_buf).strip())
            if text:
                out.append(f"<p>{text}</p>")
            paragraph_buf.clear()

    while i < len(lines):
        stripped = lines[i].strip()
        fence = _FENCE_RE.match(stripped)
        if fence:
            flush_paragraph()
            language = fence.group(1) or ""
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(code_lines)
            lang_param = (f'<ac:parameter ac:name="language">{_escape(language)}</ac:parameter>'
                         if language else "")
            out.append(
                '<ac:structured-macro ac:name="code">' + lang_param
                + f'<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>'
                '</ac:structured-macro>'
            )
            continue
        if not stripped:
            flush_paragraph()
            i += 1
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline_to_storage(heading.group(2))}</h{level}>")
            i += 1
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            flush_paragraph()
            items = [bullet.group(1)]
            i += 1
            while i < len(lines) and _BULLET_RE.match(lines[i].strip()):
                items.append(_BULLET_RE.match(lines[i].strip()).group(1))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline_to_storage(it)}</li>" for it in items) + "</ul>")
            continue
        number = _NUMBER_RE.match(stripped)
        if number:
            flush_paragraph()
            items = [number.group(1)]
            i += 1
            while i < len(lines) and _NUMBER_RE.match(lines[i].strip()):
                items.append(_NUMBER_RE.match(lines[i].strip()).group(1))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline_to_storage(it)}</li>" for it in items) + "</ol>")
            continue
        paragraph_buf.append(stripped)
        i += 1
    flush_paragraph()
    return "".join(out)


__all__ = ["ConfluenceClient", "ConfluenceError", "storage_to_markdown", "markdown_to_storage"]
