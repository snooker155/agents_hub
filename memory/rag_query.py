"""
RAG query module — used by agents (subprocesses) to search the vector store.

Reads configuration from the .env file directly so it works correctly in
subprocesses where os.environ is frozen at launch time.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _PROJECT_ROOT / ".env"
_CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _read_env(key: str, default: str = "") -> str:
    """Read from os.environ first, then fall back to .env file."""
    val = os.environ.get(key, "")
    if val:
        return val.strip().strip("\"'")
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("\"'")
    return default


def is_rag_configured() -> bool:
    return (
        _read_env("RAG_VECTOR_DB", "none") not in ("none", "")
        and _read_env("RAG_EMBEDDING_PROVIDER", "none") not in ("none", "")
    )


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_query(text: str) -> Optional[list]:
    """Embed a single query text using the configured provider."""
    provider = _read_env("RAG_EMBEDDING_PROVIDER", "none")
    model = _read_env("RAG_EMBEDDING_MODEL", "")
    if provider in ("none", "") or not model:
        return None
    try:
        if provider == "sentence-transformers":
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(model).encode([text])[0].tolist()

        if provider == "ollama":
            import requests
            base = _read_env("RAG_EMBEDDING_BASE_URL", "http://localhost:11434")
            resp = requests.post(
                f"{base.rstrip('/')}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["embedding"]

        if provider == "openai":
            import openai
            key = _read_env("RAG_EMBEDDING_API_KEY") or _read_env("OPENAI_API_KEY")
            client = openai.OpenAI(api_key=key)
            return client.embeddings.create(input=[text], model=model).data[0].embedding

        if provider == "google":
            import google.generativeai as genai
            key = _read_env("RAG_EMBEDDING_API_KEY") or _read_env("GOOGLE_API_KEY")
            genai.configure(api_key=key)
            return genai.embed_content(model=model, content=text, task_type="retrieval_query")["embedding"]
    except Exception:
        return None
    return None


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_rag(query: str, memory_id: str, top_k: int = 5) -> list[dict]:
    """Embed *query* and return the top-k relevant chunks from *memory_id*."""
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


def inject_rag_context(agent_id: str, instruction: str) -> str:
    """
    Return a context block to prepend to *instruction*, or "" when nothing
    relevant is found or RAG is not configured for this agent.
    """
    if not is_rag_configured():
        return ""
    try:
        from agents.registry import get_agent as _get_agent
        spec = _get_agent(agent_id)
        if not (spec and spec.memory_type == "shared" and spec.memory_data):
            return ""
        pool_id = str(spec.memory_data)
        results = search_rag(instruction, pool_id, top_k=5)
        if not results:
            return ""
        chunks = [r["text"] for r in results if r.get("text", "").strip()]
        if not chunks:
            return ""
        block = "\n\n---\n\n".join(chunks)
        return (
            "## Relevant context retrieved from memory (semantic search):\n\n"
            + block
            + "\n\n---\n\n"
        )
    except Exception:
        return ""
