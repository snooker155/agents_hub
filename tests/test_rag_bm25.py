"""Keyword search (dashboard/backend/rag/bm25.py) over dashboard/backend/rag/chunk_store.py.

Pure-Python BM25, no dependency, reading only from ``rag_chunks`` — this is
what answers a search when no vector store or embedding provider is
configured at all. These tests cover the tokenizer, the ranking itself (a
chunk that repeats the query term ranks above one that only mentions it once),
and the per-pool cache: built once, reused, and dropped on
``invalidate_pool`` so a write to ``rag_chunks`` is visible on the next
search.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from rag import bm25  # noqa: E402
from rag.chunking import Chunk  # noqa: E402
from rag.chunk_store import replace_file_chunks  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_bm25_cache():
    bm25.clear_cache()
    yield
    bm25.clear_cache()


def _seed(pool_id: str, filename: str, texts: list[str]) -> None:
    chunks = [Chunk(text=t, heading_path=[], char_start=0, char_end=len(t)) for t in texts]
    file_id = f"{pool_id}::{filename}"
    replace_file_chunks(pool_id, filename, file_id, chunks, content_hash=f"hash-{filename}")


# ── tokenizer ────────────────────────────────────────────────────────────────

def test_tokenize_lowercases_and_finds_unicode_word_chars():
    assert bm25.tokenize("Café Résumé") == ["café", "résumé"]


def test_tokenize_can_drop_stopwords():
    tokens = bm25.tokenize("the quick fox and the dog", drop_stopwords=True)
    assert "the" not in tokens
    assert "and" not in tokens
    assert "quick" in tokens and "fox" in tokens


# ── ranking ──────────────────────────────────────────────────────────────────

def test_a_chunk_that_repeats_the_term_outranks_one_mention():
    _seed("pool-bm25", "doc.md", [
        "widgets are useful widgets everywhere, widgets galore",
        "this document briefly mentions widgets once",
        "completely unrelated content about gardening",
    ])
    hits = bm25.search_pool("pool-bm25", "widgets", top_k=5)
    assert len(hits) == 2
    assert "widgets galore" in hits[0]["text"]


def test_no_query_terms_after_tokenizing_returns_nothing():
    _seed("pool-bm25b", "doc.md", ["some content here"])
    assert bm25.search_pool("pool-bm25b", "   ", top_k=5) == []


def test_a_pool_with_no_chunks_returns_nothing():
    assert bm25.search_pool("pool-empty", "anything", top_k=5) == []


def test_hits_carry_the_chunk_row_fields():
    _seed("pool-bm25c", "notes.md", ["alpha beta gamma"])
    hits = bm25.search_pool("pool-bm25c", "alpha", top_k=5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["filename"] == "notes.md"
    assert hit["file_id"] == "pool-bm25c::notes.md"
    assert hit["chunk_index"] == 0
    assert "bm25_score" in hit and hit["bm25_score"] > 0


# ── caching ──────────────────────────────────────────────────────────────────

def test_the_index_is_cached_between_searches():
    _seed("pool-cache", "a.md", ["searchable content one"])
    bm25.pool_index("pool-cache")
    index1, _ = bm25.pool_index("pool-cache")
    index2, _ = bm25.pool_index("pool-cache")
    assert index1 is index2  # same cached object, not rebuilt


def test_invalidate_pool_makes_a_new_write_visible():
    _seed("pool-inval", "a.md", ["first version of the document"])
    before = bm25.search_pool("pool-inval", "first", top_k=5)
    assert len(before) == 1

    # Overwrite without invalidating: the stale cached index still answers
    # with the old corpus.
    _seed("pool-inval", "a.md", ["completely different content now"])
    stale = bm25.search_pool("pool-inval", "first", top_k=5)
    assert len(stale) == 1  # cache not yet invalidated

    bm25.invalidate_pool("pool-inval")
    after = bm25.search_pool("pool-inval", "first", top_k=5)
    assert after == []
    after2 = bm25.search_pool("pool-inval", "different", top_k=5)
    assert len(after2) == 1


def test_invalidate_pool_does_not_touch_other_pools():
    _seed("pool-x", "a.md", ["content for pool x"])
    _seed("pool-y", "a.md", ["content for pool y"])
    bm25.pool_index("pool-x")
    bm25.pool_index("pool-y")
    bm25.invalidate_pool("pool-x")
    # pool-y's cached index object should be untouched.
    index_y_before = bm25._CACHE["pool-y"]
    bm25.pool_index("pool-y")
    assert bm25._CACHE["pool-y"] is index_y_before
