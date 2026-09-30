"""The vector store: ids that stay the same, and deletes that happen at all.

Two bugs the audit found, both invisible until the numbers stopped making
sense. Chunk ids were derived from ``hash()``, which Python salts per process,
so re-indexing a file wrote a second copy of every chunk under new ids instead
of overwriting the old ones. And nothing ever deleted: a removed file, a
de-indexed one and a deleted pool all left their chunks answering searches.

Chroma is the default store and the one installed here; the remote stores share
the same id scheme and the same delete dispatcher.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

chromadb = pytest.importorskip("chromadb", reason="chromadb is an optional RAG dependency")

from rag.embeddings import EmbeddingResult  # noqa: E402
from rag.vector_store import (  # noqa: E402
    chunk_id,
    delete_chroma,
    delete_vectors,
    split_file_id,
    upsert_chroma,
)

COLLECTION = "test_rag"


@pytest.fixture(autouse=True)
def local_chroma(tmp_path, monkeypatch):
    """Point the persistent client at a throwaway directory."""
    import rag.vector_store as vs

    monkeypatch.setattr(vs, "_CHROMA_PATH", str(tmp_path / "chroma"))
    yield


def _embeddings(count: int) -> EmbeddingResult:
    return EmbeddingResult(
        vectors=[[0.1 * (i + 1), 0.2, 0.3] for i in range(count)],
        model="test",
        provider="test",
    )


def _collection():
    client = chromadb.PersistentClient(path=chunk_path())
    return client.get_or_create_collection(COLLECTION)


def chunk_path() -> str:
    import rag.vector_store as vs

    return vs._CHROMA_PATH


def _stored(file_id: str | None = None) -> dict:
    where = {"file_id": file_id} if file_id else None
    got = _collection().get(where=where) if where else _collection().get()
    return got


# ── ids ──────────────────────────────────────────────────────────────────────

def test_a_chunk_id_is_the_same_every_time():
    """The property the whole re-index story rests on."""
    assert chunk_id("pool::doc.md", 3) == chunk_id("pool::doc.md", 3)
    assert chunk_id("pool::doc.md", 3) != chunk_id("pool::doc.md", 4)
    assert chunk_id("pool::doc.md", 3) != chunk_id("other::doc.md", 3)


def test_the_file_id_carries_the_pool():
    assert split_file_id("pool-a::notes.md") == ("pool-a", "notes.md")
    assert split_file_id("legacy-name") == ("", "legacy-name")


# ── re-ingestion ─────────────────────────────────────────────────────────────

def test_re_ingesting_the_same_file_upserts_instead_of_duplicating():
    chunks = ["alpha chunk", "beta chunk"]
    file_id = "pool-1::doc.md"

    upsert_chroma(COLLECTION, "", chunks, _embeddings(2), file_id)
    first = _stored(file_id)["ids"]

    upsert_chroma(COLLECTION, "", chunks, _embeddings(2), file_id)
    second = _stored(file_id)

    assert sorted(first) == sorted(second["ids"])
    assert len(second["ids"]) == 2


def test_an_edited_file_that_shrinks_leaves_no_tail_behind():
    """Re-indexing a file that lost a paragraph used to keep the paragraph:
    the old chunk kept its id and nothing overwrote it."""
    file_id = "pool-1::doc.md"
    upsert_chroma(COLLECTION, "", ["one", "two", "three"], _embeddings(3), file_id)
    assert len(_stored(file_id)["ids"]) == 3

    upsert_chroma(COLLECTION, "", ["one", "two"], _embeddings(2), file_id)
    stored = _stored(file_id)
    assert len(stored["ids"]) == 2
    assert "three" not in stored["documents"]


def test_every_chunk_carries_its_pool_and_source():
    upsert_chroma(COLLECTION, "", ["only chunk"], _embeddings(1), "pool-1::doc.md")
    meta = _stored("pool-1::doc.md")["metadatas"][0]
    assert meta["pool_id"] == "pool-1"
    assert meta["source"] == "doc.md"
    assert meta["chunk_idx"] == 0


# ── deletion ─────────────────────────────────────────────────────────────────

def test_delete_by_source_removes_one_file_and_leaves_the_others():
    upsert_chroma(COLLECTION, "", ["a", "b"], _embeddings(2), "pool-1::gone.md")
    upsert_chroma(COLLECTION, "", ["c"], _embeddings(1), "pool-1::kept.md")

    out = delete_chroma(COLLECTION, "", file_id="pool-1::gone.md")

    assert out["deleted"] == 2
    assert _stored("pool-1::gone.md")["ids"] == []
    assert len(_stored("pool-1::kept.md")["ids"]) == 1


def test_delete_by_pool_removes_everything_that_pool_indexed():
    upsert_chroma(COLLECTION, "", ["a", "b"], _embeddings(2), "pool-1::one.md")
    upsert_chroma(COLLECTION, "", ["c"], _embeddings(1), "pool-1::two.md")
    upsert_chroma(COLLECTION, "", ["d"], _embeddings(1), "pool-2::other.md")

    out = delete_chroma(COLLECTION, "", pool_id="pool-1")

    assert out["deleted"] == 3
    assert _stored("pool-1::one.md")["ids"] == []
    assert len(_stored("pool-2::other.md")["ids"]) == 1


def test_deleting_from_a_collection_that_does_not_exist_is_not_an_error():
    assert delete_chroma("never_created", "", pool_id="pool-1")["deleted"] == 0


def test_the_dispatcher_skips_an_unconfigured_store():
    assert "skipped" in delete_vectors("none", COLLECTION, file_id="pool-1::doc.md")
    assert "skipped" in delete_vectors("mystery_db", COLLECTION, pool_id="pool-1")


def test_the_dispatcher_needs_something_to_delete():
    with pytest.raises(ValueError):
        delete_vectors("chroma", COLLECTION)


def test_the_dispatcher_reaches_chroma():
    upsert_chroma(COLLECTION, "", ["a"], _embeddings(1), "pool-9::doc.md")
    out = delete_vectors("chroma", COLLECTION, file_id="pool-9::doc.md")
    assert out["deleted"] == 1
