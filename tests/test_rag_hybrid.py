"""Hybrid search (memory/rag_query.py): BM25 always, vector similarity when
configured, fused by reciprocal rank fusion.

No torch and no chroma here — the vector side is a fake (a stubbed query
function and a stubbed embedder), so these tests run the real fusion logic
against fixtures instead of a real model or a real store. What matters is
pinned down directly: a pool with no vector store configured still answers
from BM25 alone (the whole point of keeping chunk text in ``rag_chunks``
rather than only in the vector store), a chunk both retrievers agree on
outranks one only a single retriever found, and a vector-only hit still gets
its heading_path and filename back from the chunk store.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from rag import bm25 as backend_bm25  # noqa: E402
from rag.chunking import Chunk  # noqa: E402
from rag.chunk_store import replace_file_chunks  # noqa: E402

import memory.rag_query as rag_query  # noqa: E402

POOL = "pool-hybrid"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    backend_bm25.clear_cache()
    # Never touch a real .env or a real vector provider by accident.
    for key in ("RAG_VECTOR_DB", "RAG_EMBEDDING_PROVIDER", "RAG_EMBEDDING_MODEL",
                "RAG_VECTOR_DB_URL", "RAG_VECTOR_DB_COLLECTION", "RAG_EMBEDDING_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    yield
    backend_bm25.clear_cache()


def _seed(filename: str, texts: list[str], heading_path=None) -> None:
    chunks = [
        Chunk(text=t, heading_path=list(heading_path or []), char_start=0, char_end=len(t))
        for t in texts
    ]
    file_id = f"{POOL}::{filename}"
    replace_file_chunks(POOL, filename, file_id, chunks, content_hash=f"hash-{filename}")


# ── BM25-only (no vector store configured) ───────────────────────────────────

def test_no_vector_store_configured_answers_from_bm25_alone(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")
    _seed("doc.md", ["falcons hunt at high speed over open fields"])

    results = rag_query.search_rag("falcons", POOL, top_k=5)
    assert len(results) == 1
    assert results[0]["matched"] == ["bm25"]
    assert results[0]["filename"] == "doc.md"
    assert "falcons" in results[0]["text"]


def test_no_results_when_the_pool_has_no_chunks(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")
    assert rag_query.search_rag("anything", "pool-nothing-here", top_k=5) == []


def test_embedding_call_failing_still_leaves_bm25_answering(monkeypatch):
    """RAG_VECTOR_DB configured, but the embedding call fails (missing
    package, unreachable provider, whatever) — search_rag degrades to BM25
    rather than raising or coming back empty."""
    monkeypatch.setenv("RAG_VECTOR_DB", "chroma")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "sentence-transformers")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake-model")
    monkeypatch.setattr(rag_query, "embed_query", lambda text: None)
    _seed("doc.md", ["owls hunt silently at night"])

    results = rag_query.search_rag("owls", POOL, top_k=5)
    assert len(results) == 1
    assert results[0]["matched"] == ["bm25"]


# ── Hybrid fusion ────────────────────────────────────────────────────────────

def test_a_chunk_both_retrievers_agree_on_outranks_a_single_source_hit(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "chroma")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "sentence-transformers")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake-model")

    _seed("doc.md", [
        "kestrels are small falcons that hover while hunting",   # chunk 0: agreed hit
        "kestrels also nest in old buildings and cliffs",         # chunk 1: bm25-only
    ])

    monkeypatch.setattr(rag_query, "embed_query", lambda text: [0.1, 0.2, 0.3])
    monkeypatch.setattr(rag_query, "_query_chroma", lambda vec, memory_id, top_k: [
        {"text": "kestrels are small falcons that hover while hunting",
         "file_id": f"{POOL}::doc.md", "chunk_idx": 0, "score": 0.9},
        # A vector-only hit for a chunk BM25 did not surface for this query.
        {"text": "kestrels also perch on wires near open ground",
         "file_id": f"{POOL}::other.md", "chunk_idx": 0, "score": 0.7},
    ])

    results = rag_query.search_rag("kestrels hovering hunting", POOL, top_k=5)
    assert results, "expected at least one fused result"
    top = results[0]
    assert top["file_id"] == f"{POOL}::doc.md"
    assert top["chunk_idx"] == 0
    assert set(top["matched"]) == {"bm25", "vector"}
    # The agreed-upon chunk must outrank every single-source result.
    assert all(top["score"] >= r["score"] for r in results[1:])


def test_a_vector_only_hit_is_backfilled_from_the_chunk_store(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "chroma")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "sentence-transformers")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake-model")

    _seed("structured.md", ["irrelevant chunk zero", "the vector-only target chunk"],
          heading_path=["Section"])

    monkeypatch.setattr(rag_query, "embed_query", lambda text: [0.1, 0.2, 0.3])
    # Vector store hits never carry heading_path — only file_id/chunk_idx/text/score.
    monkeypatch.setattr(rag_query, "_query_chroma", lambda vec, memory_id, top_k: [
        {"text": "the vector-only target chunk",
         "file_id": f"{POOL}::structured.md", "chunk_idx": 1, "score": 0.95},
    ])
    # No BM25 hit at all for this query (a term that appears nowhere).
    monkeypatch.setattr(rag_query, "_search_bm25", lambda query, memory_id, top_k: [])

    results = rag_query.search_rag("zzz-no-keyword-match", POOL, top_k=5)
    assert len(results) == 1
    assert results[0]["matched"] == ["vector"]
    assert results[0]["heading_path"] == ["Section"]
    assert results[0]["filename"] == "structured.md"


def test_search_rag_respects_top_k(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")
    _seed("many.md", [f"needle chunk number {i}" for i in range(10)])

    results = rag_query.search_rag("needle", POOL, top_k=3)
    assert len(results) == 3


# ── The shared Embedder (dashboard/backend/rag/embeddings.py) ───────────────

def test_embedder_for_config_returns_the_same_process_wide_instance():
    import rag.embeddings as backend_embeddings

    a = backend_embeddings.Embedder.for_config("sentence-transformers", "fake-model")
    b = backend_embeddings.Embedder.for_config("sentence-transformers", "fake-model")
    assert a is b


def test_embedder_for_config_replaces_the_instance_on_a_config_change():
    import rag.embeddings as backend_embeddings

    a = backend_embeddings.Embedder.for_config("sentence-transformers", "model-a")
    b = backend_embeddings.Embedder.for_config("sentence-transformers", "model-b")
    assert a is not b


def test_embedder_embed_one_loads_the_model_once_and_caches_the_query_vector(monkeypatch):
    import rag.embeddings as backend_embeddings

    backend_embeddings.clear_query_cache()
    calls = {"n": 0}

    def fake_embed_st(texts, model):
        calls["n"] += 1
        return backend_embeddings.EmbeddingResult(
            vectors=[[1.0, 2.0, 3.0] for _ in texts], model=model, provider="sentence-transformers",
        )

    monkeypatch.setattr(backend_embeddings, "embed_sentence_transformers", fake_embed_st)
    embedder = backend_embeddings.Embedder("sentence-transformers", "cache-test-model")

    v1 = embedder.embed_one("what is a kestrel")
    v2 = embedder.embed_one("what is a kestrel")  # should hit the query cache
    assert v1 == v2 == [1.0, 2.0, 3.0]
    assert calls["n"] == 1


def test_embedder_embed_one_returns_none_on_failure(monkeypatch):
    import rag.embeddings as backend_embeddings

    def boom(texts, model):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(backend_embeddings, "embed_sentence_transformers", boom)
    embedder = backend_embeddings.Embedder("sentence-transformers", "boom-model")
    assert embedder.embed_one("anything") is None


def test_embedder_is_configured():
    import rag.embeddings as backend_embeddings

    assert backend_embeddings.Embedder("none", "").is_configured() is False
    assert backend_embeddings.Embedder("sentence-transformers", "m").is_configured() is True
