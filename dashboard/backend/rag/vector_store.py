"""
Vector store adapters.
Each function upserts chunks (+optional embeddings) into the target store and
returns a metadata dict. Raises RuntimeError with an install hint if the
required package is missing.

Ids are deterministic: the same pool, source and chunk index always produce the
same id, so re-indexing a file overwrites its chunks instead of adding a second
copy of the document. The deletes below are the other half of that: a file that
is removed, re-indexed shorter, or whose pool is deleted leaves no orphan
vectors behind.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Tuple
from uuid import NAMESPACE_URL, uuid5
from .embeddings import EmbeddingResult

# Use a fixed absolute path so the backend and agent subprocesses share the same DB.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")


# ── Ids and metadata ──────────────────────────────────────────────────────────

def split_file_id(file_id: str) -> Tuple[str, str]:
    """``{pool_id}::{source}`` → (pool_id, source).

    A file_id written before the convention existed has no separator; it is
    then all source and belongs to no pool.
    """
    pool_id, sep, source = str(file_id or "").partition("::")
    if not sep:
        return "", str(file_id or "")
    return pool_id, source


def chunk_id(file_id: str, index: int) -> str:
    """Stable id for one chunk of one source.

    uuid5 rather than a hash of the string because Qdrant only accepts an
    unsigned integer or a UUID as a point id, and every other store accepts a
    string — one scheme covers all three.
    """
    return str(uuid5(NAMESPACE_URL, f"agents_hub/rag/{file_id}#{index}"))


def _metadatas(file_id: str, chunks: List[str]) -> List[dict]:
    pool_id, source = split_file_id(file_id)
    return [
        {"file_id": file_id, "pool_id": pool_id, "source": source, "chunk_idx": i}
        for i in range(len(chunks))
    ]


# ── ChromaDB ──────────────────────────────────────────────────────────────────

def _chroma_client(url: str):
    try:
        import chromadb
    except ImportError:
        raise RuntimeError("chromadb not installed. Run: pip install chromadb")

    if url:
        # HTTP client (remote ChromaDB server)
        from chromadb import HttpClient
        host, _, port_str = url.replace("http://", "").replace("https://", "").rpartition(":")
        host = host or url
        port = int(port_str) if port_str.isdigit() else 8000
        return HttpClient(host=host, port=port)
    # Local persistent storage — absolute path shared with agent subprocesses
    return chromadb.PersistentClient(path=_CHROMA_PATH)


def upsert_chroma(
    collection_name: str,
    url: str,
    chunks: List[str],
    embeddings: Optional[EmbeddingResult],
    file_id: str,
) -> dict:
    client = _chroma_client(url)
    collection = client.get_or_create_collection(collection_name)
    ids = [chunk_id(file_id, i) for i in range(len(chunks))]
    kwargs: dict = dict(
        ids=ids,
        documents=chunks,
        metadatas=_metadatas(file_id, chunks),
    )
    if embeddings:
        kwargs["embeddings"] = embeddings.vectors
    collection.upsert(**kwargs)
    # A re-index that produced fewer chunks than last time leaves the tail
    # behind: same source, indexes past the new end. Drop them.
    removed = _chroma_drop_tail(collection, file_id, len(chunks))
    return {"upserted": len(chunks), "collection": collection_name, "stale_removed": removed}


def _chroma_drop_tail(collection, file_id: str, keep: int) -> int:
    try:
        existing = collection.get(where={"file_id": file_id}, include=["metadatas"])
    except Exception:
        return 0
    ids = existing.get("ids") or []
    metas = existing.get("metadatas") or []
    stale = [
        _id for _id, meta in zip(ids, metas)
        if int((meta or {}).get("chunk_idx", 0)) >= keep
    ]
    if stale:
        try:
            collection.delete(ids=stale)
        except Exception:
            return 0
    return len(stale)


def delete_chroma(
    collection_name: str,
    url: str,
    *,
    file_id: Optional[str] = None,
    pool_id: Optional[str] = None,
) -> dict:
    """Delete every vector of one source (file_id) or one pool."""
    client = _chroma_client(url)
    try:
        collection = client.get_collection(collection_name)
    except Exception:
        return {"deleted": 0, "collection": collection_name}

    ids = _chroma_matching_ids(collection, file_id=file_id, pool_id=pool_id)
    if ids:
        collection.delete(ids=ids)
    return {"deleted": len(ids), "collection": collection_name}


def _chroma_matching_ids(collection, *, file_id: Optional[str], pool_id: Optional[str]) -> List[str]:
    """Ids to delete, falling back to a scan for vectors written before
    ``pool_id`` was part of the metadata."""
    if file_id:
        try:
            got = collection.get(where={"file_id": file_id})
            return list(got.get("ids") or [])
        except Exception:
            return []
    if not pool_id:
        return []
    try:
        got = collection.get(where={"pool_id": pool_id})
        ids = list(got.get("ids") or [])
    except Exception:
        ids = []
    try:
        everything = collection.get(include=["metadatas"])
    except Exception:
        return ids
    prefix = f"{pool_id}::"
    legacy = [
        _id for _id, meta in zip(everything.get("ids") or [], everything.get("metadatas") or [])
        if str((meta or {}).get("file_id", "")).startswith(prefix)
    ]
    return list(dict.fromkeys([*ids, *legacy]))


# ── Pinecone ──────────────────────────────────────────────────────────────────

def _pinecone_index(index_name: str, api_key: str):
    try:
        from pinecone import Pinecone
    except ImportError:
        raise RuntimeError(
            "pinecone-client not installed. Run: pip install pinecone-client"
        )
    if not api_key:
        raise RuntimeError("Pinecone API key not set (RAG_VECTOR_DB_API_KEY)")
    return Pinecone(api_key=api_key).Index(index_name)


def upsert_pinecone(
    index_name: str,
    api_key: str,
    chunks: List[str],
    embeddings: EmbeddingResult,  # Pinecone always requires vectors
    file_id: str,
) -> dict:
    index = _pinecone_index(index_name, api_key)
    metas = _metadatas(file_id, chunks)
    vectors = [
        {
            "id": chunk_id(file_id, i),
            "values": embeddings.vectors[i],
            "metadata": {"text": chunk, **metas[i]},
        }
        for i, chunk in enumerate(chunks)
    ]
    index.upsert(vectors=vectors)
    return {"upserted": len(chunks), "index": index_name}


def delete_pinecone(
    index_name: str,
    api_key: str,
    *,
    file_id: Optional[str] = None,
    pool_id: Optional[str] = None,
) -> dict:
    index = _pinecone_index(index_name, api_key)
    where = {"file_id": {"$eq": file_id}} if file_id else {"pool_id": {"$eq": pool_id}}
    index.delete(filter=where)
    return {"deleted": "all matching", "index": index_name}


# ── Qdrant ────────────────────────────────────────────────────────────────────

def _qdrant_client(url: str, api_key: str):
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        raise RuntimeError(
            "qdrant-client not installed. Run: pip install qdrant-client"
        )
    return QdrantClient(url=url or "http://localhost:6333", api_key=api_key or None)


def upsert_qdrant(
    collection_name: str,
    url: str,
    api_key: str,
    chunks: List[str],
    embeddings: EmbeddingResult,  # Qdrant always requires vectors
    file_id: str,
) -> dict:
    from qdrant_client.models import PointStruct, VectorParams, Distance

    client = _qdrant_client(url, api_key)
    dims = embeddings.dims

    # Create collection if it doesn't exist yet
    try:
        client.get_collection(collection_name)
    except Exception:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
        )

    metas = _metadatas(file_id, chunks)
    points = [
        PointStruct(
            # Deterministic uuid5 instead of the old abs(hash(...)): Python's
            # string hash is salted per process, so the same chunk landed under
            # a different id on every run and re-indexing duplicated the file.
            id=chunk_id(file_id, i),
            vector=embeddings.vectors[i],
            payload={"text": chunk, **metas[i]},
        )
        for i, chunk in enumerate(chunks)
    ]
    client.upsert(collection_name=collection_name, points=points)
    return {"upserted": len(chunks), "collection": collection_name}


def delete_qdrant(
    collection_name: str,
    url: str,
    api_key: str,
    *,
    file_id: Optional[str] = None,
    pool_id: Optional[str] = None,
) -> dict:
    from qdrant_client.models import FilterSelector, Filter, FieldCondition, MatchValue

    client = _qdrant_client(url, api_key)
    key, value = ("file_id", file_id) if file_id else ("pool_id", pool_id)
    selector = FilterSelector(
        filter=Filter(must=[FieldCondition(key=key, match=MatchValue(value=value))])
    )
    try:
        client.delete(collection_name=collection_name, points_selector=selector)
    except Exception as exc:  # collection may not exist yet
        return {"deleted": 0, "collection": collection_name, "error": str(exc)}
    return {"deleted": "all matching", "collection": collection_name}


# ── Provider-agnostic delete ─────────────────────────────────────────────────

def delete_vectors(
    db: str,
    collection: str,
    url: str = "",
    api_key: str = "",
    *,
    file_id: Optional[str] = None,
    pool_id: Optional[str] = None,
) -> dict:
    """Delete the vectors of one source or one pool from the configured store.

    Exactly one of ``file_id`` (one indexed file) and ``pool_id`` (everything a
    pool ever indexed) is expected. Returns a metadata dict; an unconfigured or
    unknown store is a no-op rather than an error, so callers can delete
    unconditionally.
    """
    if not file_id and not pool_id:
        raise ValueError("delete_vectors needs file_id or pool_id")
    db = (db or "none").lower()
    if db in ("", "none"):
        return {"deleted": 0, "skipped": "no vector store configured"}
    if db == "chroma":
        return delete_chroma(collection, url, file_id=file_id, pool_id=pool_id)
    if db == "qdrant":
        return delete_qdrant(collection, url, api_key, file_id=file_id, pool_id=pool_id)
    if db == "pinecone":
        return delete_pinecone(collection, api_key, file_id=file_id, pool_id=pool_id)
    return {"deleted": 0, "skipped": f"unknown vector DB: {db}"}
