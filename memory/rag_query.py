"""
RAG query module — used by agents (subprocesses) to search a pool's indexed
knowledge.

``search_rag`` is hybrid: BM25 over the pool's chunks (``rag_chunks``, read
through the backend's ``rag.bm25`` module) always runs, and vector similarity
runs alongside it whenever a vector store and an embedding provider are both
configured. The two rankings are fused with reciprocal rank fusion. When no
vector store is configured, or the embedding call fails for any reason (the
library is missing, the provider is unreachable), BM25 answers alone — a pool
with RAG_VECTOR_DB=none still gets ranked keyword search with no torch and no
running vector DB.

Reads configuration from the .env file directly so it works correctly in
subprocesses where os.environ is frozen at launch time. The backend's own
``rag`` package (chunk store, BM25, the shared Embedder, the vector store
adapters) is imported by path when this runs outside the dashboard backend
process, the same way ``delete_rag_vectors`` always has.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from common.paths import PROJECT_ROOT
from common.dotenv import read_env as _read_dot_env

_PROJECT_ROOT = PROJECT_ROOT
_CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")

# Reciprocal rank fusion constant — the same value memory/ranking.py uses to
# fuse BM25 with a vector-store ranking at the pool level; here it fuses BM25
# with vector similarity at the chunk level, one layer down.
_RRF_K = 60


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _read_env(key: str, default: str = "") -> str:
    """Read from os.environ first, then fall back to .env file (cached)."""
    val = os.environ.get(key, "")
    if val:
        return val.strip().strip("\"'")
    return _read_dot_env().get(key, default)


def is_rag_configured() -> bool:
    return (
        _read_env("RAG_VECTOR_DB", "none") not in ("none", "")
        and _read_env("RAG_EMBEDDING_PROVIDER", "none") not in ("none", "")
    )


def _backend_module(name: str):
    """Import ``dashboard/backend/rag/<name>.py``. Works unmodified when this
    process already has the backend directory on ``sys.path`` (the dashboard
    backend itself); an agent subprocess does not, so the directory is added
    on first need — the same fallback ``delete_rag_vectors`` has always used,
    generalised to any module of the package rather than just ``vector_store``.
    """
    try:
        return __import__(f"rag.{name}", fromlist=["_"])
    except Exception:
        pass
    backend = _PROJECT_ROOT / "dashboard" / "backend"
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    return __import__(f"rag.{name}", fromlist=["_"])


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_query(text: str) -> Optional[list]:
    """Embed a single query text using the configured provider, through the
    process-wide :class:`~rag.embeddings.Embedder` so the model behind it
    (sentence-transformers weights, mainly) is loaded once and shared with
    ingestion rather than a second copy living in this module.

    Returns None for no provider configured, a missing package, or any other
    failure — the caller falls back to BM25-only search rather than raising.
    """
    provider = _read_env("RAG_EMBEDDING_PROVIDER", "none")
    model = _read_env("RAG_EMBEDDING_MODEL", "")
    if provider in ("none", "") or not model:
        return None
    try:
        api_key = _read_env("RAG_EMBEDDING_API_KEY", "")
        if not api_key and provider == "openai":
            api_key = _read_env("OPENAI_API_KEY", "")
        if not api_key and provider == "google":
            api_key = _read_env("GOOGLE_API_KEY", "")
        base_url = _read_env("RAG_EMBEDDING_BASE_URL", "http://localhost:11434")
        Embedder = _backend_module("embeddings").Embedder
        embedder = Embedder.for_config(provider, model, api_key=api_key, base_url=base_url)
        return embedder.embed_one(text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Keyword (BM25) search
# ---------------------------------------------------------------------------

def _search_bm25(query: str, memory_id: str, top_k: int) -> list[dict]:
    try:
        bm25 = _backend_module("bm25")
        hits = bm25.search_pool(str(memory_id), query, top_k=top_k)
    except Exception:
        return []
    return [
        {
            "text": h.get("text", ""),
            "file_id": h.get("file_id", ""),
            "filename": h.get("filename", ""),
            "chunk_idx": h.get("chunk_index", 0),
            "heading_path": h.get("heading_path") or [],
            "bm25_score": h.get("bm25_score", 0.0),
        }
        for h in hits
    ]


# ---------------------------------------------------------------------------
# Vector store query
# ---------------------------------------------------------------------------

def _filter_by_memory(results: list[dict], memory_id: str) -> list[dict]:
    """Keep only results whose file_id belongs to the given memory pool."""
    prefix = f"{memory_id}::"
    return [r for r in results if r.get("file_id", "").startswith(prefix)]


def _query_chroma(query_vector: list, memory_id: str, top_k: int) -> list[dict]:
    import chromadb  # type: ignore

    url = _read_env("RAG_VECTOR_DB_URL", "")
    collection_name = _read_env("RAG_VECTOR_DB_COLLECTION", "agents_hub_rag")

    if url:
        host_part = url.replace("http://", "").replace("https://", "")
        host, _, port_str = host_part.rpartition(":")
        port = int(port_str) if port_str.isdigit() else 8000
        client = chromadb.HttpClient(host=host or host_part, port=port)
    else:
        client = chromadb.PersistentClient(path=_CHROMA_PATH)

    try:
        collection = client.get_collection(collection_name)
    except Exception:
        return []

    # Fetch more than needed so we can filter by memory_id afterwards
    fetch = max(top_k * 4, 20)
    res = collection.query(
        query_embeddings=[query_vector],
        n_results=fetch,
        include=["documents", "metadatas", "distances"],
    )
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    distances = res.get("distances", [[]])[0]

    rows = [
        {"text": doc, "file_id": meta.get("file_id", ""), "chunk_idx": meta.get("chunk_idx", 0), "score": round(1.0 - dist, 4)}
        for doc, meta, dist in zip(docs, metas, distances)
    ]
    return _filter_by_memory(rows, memory_id)[:top_k]


def _query_qdrant(query_vector: list, memory_id: str, top_k: int) -> list[dict]:
    from qdrant_client import QdrantClient  # type: ignore

    url = _read_env("RAG_VECTOR_DB_URL", "http://localhost:6333")
    api_key = _read_env("RAG_VECTOR_DB_API_KEY") or None
    collection_name = _read_env("RAG_VECTOR_DB_COLLECTION", "agents_hub_rag")

    client = QdrantClient(url=url, api_key=api_key)
    fetch = max(top_k * 4, 20)

    try:
        hits = client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=fetch,
            with_payload=True,
        )
    except Exception:
        return []

    rows = [
        {
            "text": h.payload.get("text", ""),
            "file_id": h.payload.get("file_id", ""),
            "chunk_idx": h.payload.get("chunk_idx", 0),
            "score": round(h.score, 4),
        }
        for h in hits
    ]
    return _filter_by_memory(rows, memory_id)[:top_k]


def _query_pinecone(query_vector: list, memory_id: str, top_k: int) -> list[dict]:
    from pinecone import Pinecone  # type: ignore

    api_key = _read_env("RAG_VECTOR_DB_API_KEY", "")
    index_name = _read_env("RAG_VECTOR_DB_COLLECTION", "agents_hub_rag")

    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)
    fetch = max(top_k * 4, 20)

    res = index.query(vector=query_vector, top_k=fetch, include_metadata=True)
    rows = [
        {
            "text": m.metadata.get("text", ""),
            "file_id": m.metadata.get("file_id", ""),
            "chunk_idx": m.metadata.get("chunk_idx", 0),
            "score": round(m.score, 4),
        }
        for m in res.matches
    ]
    return _filter_by_memory(rows, memory_id)[:top_k]


def _search_vector(query: str, memory_id: str, top_k: int) -> list[dict]:
    """Vector similarity search, or an empty list when unconfigured, the
    embedding call failed, or the store adapter raised."""
    if not is_rag_configured():
        return []
    vec = embed_query(query)
    if vec is None:
        return []
    db = _read_env("RAG_VECTOR_DB", "none")
    try:
        if db == "chroma":
            return _query_chroma(vec, memory_id, top_k)
        if db == "qdrant":
            return _query_qdrant(vec, memory_id, top_k)
        if db == "pinecone":
            return _query_pinecone(vec, memory_id, top_k)
    except Exception:
        return []
    return []


# ---------------------------------------------------------------------------
# Hybrid fusion
# ---------------------------------------------------------------------------

def _chunk_key(file_id: str, chunk_idx) -> str:
    return f"{file_id}#{chunk_idx}"


def _fuse(bm25_hits: list[dict], vector_hits: list[dict], top_k: int) -> list[dict]:
    """Reciprocal rank fusion of the two ranked lists, by (file_id, chunk_idx).

    Each result in the output carries ``matched``: which retriever(s) placed
    it — ``["bm25"]``, ``["vector"]`` or both — so a caller can tell a result
    both agreed on from one only a single retriever surfaced.
    """
    scores: dict[str, float] = {}
    info: dict[str, dict] = {}
    matched: dict[str, set] = {}

    def add(hits: list[dict], source: str) -> None:
        for rank, hit in enumerate(hits, start=1):
            file_id = hit.get("file_id", "")
            chunk_idx = hit.get("chunk_idx", 0)
            key = _chunk_key(file_id, chunk_idx)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            matched.setdefault(key, set()).add(source)
            entry = info.setdefault(key, {
                "text": hit.get("text", ""),
                "file_id": file_id,
                "filename": hit.get("filename") or file_id.rpartition("::")[2],
                "chunk_idx": chunk_idx,
                "heading_path": hit.get("heading_path") or [],
            })
            if not entry.get("text"):
                entry["text"] = hit.get("text", "")

    add(bm25_hits, "bm25")
    add(vector_hits, "vector")

    # A vector-only hit carries no heading_path (the vector store keeps only
    # ids and vectors) — backfill it from the chunk store when that chunk is
    # still indexed there.
    missing = [k for k, v in matched.items() if "bm25" not in v and not info[k]["heading_path"]]
    if missing:
        try:
            store = _backend_module("chunk_store")
            for key in missing:
                file_id, _, idx = key.rpartition("#")
                row = store.get_chunk(file_id, int(idx))
                if row:
                    info[key]["heading_path"] = row["heading_path"]
                    info[key]["filename"] = row["filename"]
                    if not info[key]["text"]:
                        info[key]["text"] = row["text"]
        except Exception:
            pass

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    out: list[dict] = []
    for key, score in ranked[:max(1, int(top_k))]:
        entry = info[key]
        out.append({
            "text": entry["text"],
            "file_id": entry["file_id"],
            "filename": entry["filename"],
            "chunk_idx": entry["chunk_idx"],
            "heading_path": entry["heading_path"],
            "score": round(score, 6),
            "matched": sorted(matched[key]),
        })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_rag(query: str, memory_id: str, top_k: int = 5) -> list[dict]:
    """Hybrid search over *memory_id*'s indexed chunks: BM25 always, vector
    similarity when configured, fused by reciprocal rank fusion.

    Each result carries ``text``, ``filename``, ``heading_path``,
    ``chunk_idx``, ``score`` and ``matched`` (which retriever(s) found it).
    Works with no vector store or embedding model configured at all — BM25
    alone then answers directly from the chunk store.
    """
    fetch = max(int(top_k) * 4, 20)
    bm25_hits = _search_bm25(query, memory_id, fetch)
    vector_hits = _search_vector(query, memory_id, fetch)
    return _fuse(bm25_hits, vector_hits, top_k)


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def delete_rag_vectors(memory_id: str, filename: Optional[str] = None) -> dict:
    """Delete one indexed file's chunks and vectors, or every one of
    *memory_id*'s.

    Returns a metadata dict; a vector store that is not configured, not
    installed or not reachable comes back as a skip rather than an exception,
    because this runs inside `forget`, where a vector problem must not lose
    the delete. The chunk-store side is a local table and always succeeds.
    """
    try:
        chunk_store = _backend_module("chunk_store")
        bm25 = _backend_module("bm25")
        if filename:
            chunk_store.delete_file_chunks(f"{memory_id}::{filename}")
        else:
            chunk_store.delete_pool_chunks(str(memory_id))
        bm25.invalidate_pool(str(memory_id))
    except Exception:
        pass

    db = _read_env("RAG_VECTOR_DB", "none")
    if db in ("none", ""):
        return {"deleted": 0, "skipped": "no vector store configured"}
    try:
        delete_vectors = _backend_module("vector_store").delete_vectors
        kwargs = (
            {"file_id": f"{memory_id}::{filename}"} if filename else {"pool_id": str(memory_id)}
        )
        return delete_vectors(
            db,
            _read_env("RAG_VECTOR_DB_COLLECTION", "agents_hub_rag"),
            _read_env("RAG_VECTOR_DB_URL", ""),
            _read_env("RAG_VECTOR_DB_API_KEY", ""),
            **kwargs,
        )
    except Exception as exc:
        return {"deleted": 0, "skipped": f"vector delete failed: {exc}"}
