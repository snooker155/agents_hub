"""
Documentation tools — how an agent answers "what is this service, and how does
this part of it work" from the product's own documentation rather than from
whatever it can infer.

The corpus is ``docs/*.md`` at the repository root, with ``docs/index.json``
listing each file's title, summary, surface and links. Both are shipped with the
product, so this is the same text for every install and there is nothing to
configure.

Two tools, deliberately small:

* ``search_docs`` ranks the corpus against a query and returns the matches with
  a snippet, so the agent can tell which document is the right one before
  spending context on it. With no query it lists everything.
* ``read_doc`` returns one document whole.

Both are read-only over files the product ships. They grant no capability: this
is public product documentation, not operator data, which is why every system
agent can hold them without affecting what else it may hold.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from common.paths import PROJECT_ROOT

DOCS_DIR = PROJECT_ROOT / "docs"
INDEX_FILE = DOCS_DIR / "index.json"

#: A document is returned whole, but a corpus is not a place for a 100kB file.
MAX_DOC_CHARS = 20_000
#: Snippet around the best match in a search result.
SNIPPET_CHARS = 280

#: Words too common in this corpus to help ranking.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on",
    "how", "what", "do", "does", "can", "i", "my", "with", "that", "this",
    "are", "be", "by", "from", "at", "as", "if", "when", "which", "you",
})


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request",
              extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


@lru_cache(maxsize=1)
def _index() -> List[Dict[str, Any]]:
    """The doc index, read once per process. Empty when the corpus is missing,
    so a trimmed deployment degrades to "no documentation" rather than errors."""
    try:
        return list(json.loads(INDEX_FILE.read_text(encoding="utf-8")).get("docs") or [])
    except Exception:
        return []


@lru_cache(maxsize=64)
def _body(doc_id: str) -> str:
    path = DOCS_DIR / f"{doc_id}.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


#: Endings after which a plural "es" is the whole suffix ("matches" -> "match").
#: Everywhere else only the "s" is, which is what keeps "workspaces" foldable
#: onto "workspace" instead of onto "workspac" — a stem no query ever produces,
#: since the singular never ends in "s".
_SIBILANT_ENDINGS = ("ses", "xes", "zes", "ches", "shes")


def _stem(word: str) -> str:
    """Crude plural folding, enough for an English technical corpus.

    Without it "capability" misses a document titled "Tools and capabilities",
    which is the exact question this corpus exists to answer. Deliberately does
    not touch verb endings: folding "running" would cost more in false matches
    than it buys.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(_SIBILANT_ENDINGS):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> List[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9_]+", (text or "").lower())]


def _terms(query: str) -> List[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9_]+", (query or "").lower())
            if len(t) > 1 and t not in _STOPWORDS]


@lru_cache(maxsize=64)
def _body_tokens(doc_id: str) -> Tuple[str, ...]:
    return tuple(_tokens(_body(doc_id)))


def _score(entry: Dict[str, Any], terms: List[str], phrase: str) -> Tuple[float, str]:
    """Rank one document, and return the snippet that justifies the rank.

    Weighted by where a term appears: a title match is a much stronger signal
    than a body mention, because the corpus is one document per concept and the
    concept's name is the title.
    """
    doc_id = str(entry.get("id", ""))
    id_tokens = set(_tokens(doc_id.replace("-", " ")))
    title_tokens = set(_tokens(entry.get("title", "")))
    heading_tokens = set(_tokens(" ".join(entry.get("headings") or [])))
    summary_tokens = set(_tokens(entry.get("summary", "")))
    body = _body(doc_id)
    body_lower = body.lower()
    body_tokens = _body_tokens(doc_id)

    score = 0.0
    for term in terms:
        if term in id_tokens:
            score += 4
        if term in title_tokens:
            score += 3
        if term in heading_tokens:
            score += 2
        if term in summary_tokens:
            score += 2
        score += min(body_tokens.count(term), 5) * 0.5

    # An exact phrase is worth more than its words scattered across the file.
    if phrase and len(phrase) > 4 and phrase in body_lower:
        score += 3

    snippet = ""
    if body and terms:
        # Stems are for ranking; the snippet is located on the raw text, so a
        # stemmed term is searched as a prefix.
        pos = -1
        for term in terms:
            pos = body_lower.find(term)
            if pos != -1:
                break
        if pos != -1:
            start = max(0, pos - SNIPPET_CHARS // 3)
            snippet = " ".join(body[start:start + SNIPPET_CHARS].split())
            if start > 0:
                snippet = "…" + snippet
    return score, snippet


class SearchDocsInput(BaseModel):
    query: str = Field(
        "", description="What to look for. Plain words, e.g. 'how do loops stop' "
                        "or 'capability guard'. Leave empty to list every document.")
    limit: int = Field(5, ge=1, le=26, description="How many matches to return")


@tool("search_docs", args_schema=SearchDocsInput)
def search_docs(query: str = "", limit: int = 5) -> str:
    """Search this service's own documentation and return the best matches.

    Use it whenever you are asked how the service works, what a page or an
    object is for, or how to do something in it — including questions about
    parts of the product you have nothing to do with. Answer from what this
    returns, not from memory or inference.

    Each match carries an id, a title, a one-line summary, which page it is
    about, and a snippet showing why it matched. Read the promising one with
    read_doc rather than guessing from the snippet.

    With an empty query it lists the whole corpus, which is the fastest way to
    see what documentation exists at all.
    """
    entries = _index()
    if not entries:
        return _json_err(
            "No documentation corpus is installed (docs/index.json is missing). "
            "Say so rather than answering from memory.",
            code="no_corpus")

    terms = _terms(query)
    if not terms:
        return _json_ok({
            "query": query,
            "matches": [
                {"id": e["id"], "title": e["title"], "summary": e["summary"],
                 "surface": e.get("surface")}
                for e in entries
            ],
            "total": len(entries),
            "note": "The whole corpus. Use read_doc with an id to read one.",
        })

    phrase = " ".join(terms)
    ranked = []
    for entry in entries:
        score, snippet = _score(entry, terms, phrase)
        if score > 0:
            ranked.append((score, entry, snippet))
    ranked.sort(key=lambda r: -r[0])

    if not ranked:
        return _json_ok({
            "query": query,
            "matches": [],
            "note": "Nothing in the corpus matches. Say that plainly instead of "
                    "inventing an answer; the documents that do exist are "
                    "listed by calling this tool with an empty query.",
        })

    return _json_ok({
        "query": query,
        "matches": [
            {"id": e["id"], "title": e["title"], "summary": e["summary"],
             "surface": e.get("surface"), "related": e.get("related") or [],
             "score": round(score, 1), "snippet": snippet}
            for score, e, snippet in ranked[:limit]
        ],
        "total": len(ranked),
    })


class ReadDocInput(BaseModel):
    doc_id: str = Field(..., description="Document id from search_docs, e.g. 'loops'")


@tool("read_doc", args_schema=ReadDocInput)
def read_doc(doc_id: str) -> str:
    """Return one documentation page in full, by id.

    Ids come from search_docs. Quote or paraphrase what this returns when
    explaining the service; if it does not cover what was asked, say so rather
    than filling the gap.
    """
    doc_id = (doc_id or "").strip().removesuffix(".md")
    entry = next((e for e in _index() if e.get("id") == doc_id), None)
    if entry is None:
        available = [e["id"] for e in _index()]
        return _json_err(
            f"No document '{doc_id}'.",
            code="not_found",
            extra={"available": available} if available else None,
        )
    content = _body(doc_id)
    truncated = False
    if len(content) > MAX_DOC_CHARS:
        content = content[:MAX_DOC_CHARS]
        truncated = True
    return _json_ok({
        "id": doc_id,
        "title": entry.get("title"),
        "surface": entry.get("surface"),
        "related": entry.get("related") or [],
        "content": content,
        "truncated": truncated,
    })


DOCS_TOOLS = [search_docs, read_doc]

__all__ = ["search_docs", "read_doc", "DOCS_TOOLS", "DOCS_DIR", "INDEX_FILE"]
