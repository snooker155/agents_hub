"""
Vector store adapters.
Each function upserts chunks (+optional embeddings) into the target store and
returns a metadata dict. Raises RuntimeError with an install hint if the
required package is missing.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional
from .embeddings import EmbeddingResult

# Use a fixed absolute path so the backend and agent subprocesses share the same DB.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")


# ── ChromaDB ──────────────────────────────────────────────────────────────────

def upsert_chroma(
    collection_name: str,
    url: str,
    chunks: List[str],
    embeddings: Optional[EmbeddingResult],
    file_id: str,
) -> dict:
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
        client = HttpClient(host=host, port=port)
    else:
        # Local persistent storage — absolute path shared with agent subprocesses
        client = chromadb.PersistentClient(path=_CHROMA_PATH)

    collection = client.get_or_create_collection(collection_name)
    ids = [f"{file_id}_chunk_{i}" for i in range(len(chunks))]
    kwargs: dict = dict(
        ids=ids,
        documents=chunks,
        metadatas=[{"file_id": file_id, "chunk_idx": i} for i in range(len(chunks))],
    )
    if embeddings:
        kwargs["embeddings"] = embeddings.vectors
    collection.upsert(**kwargs)
    return {"upserted": len(chunks), "collection": collection_name}


# ── Pinecone ──────────────────────────────────────────────────────────────────

def upsert_pinecone(
    index_name: str,
    api_key: str,
    chunks: List[str],
    embeddings: EmbeddingResult,  # Pinecone always requires vectors
    file_id: str,
) -> dict:
    try:
        from pinecone import Pinecone
    except ImportError:
        raise RuntimeError(
            "pinecone-client not installed. Run: pip install pinecone-client"
        )
    if not api_key:
        raise RuntimeError("Pinecone API key not set (RAG_VECTOR_DB_API_KEY)")

    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)
    vectors = [
        {
            "id": f"{file_id}_chunk_{i}",
            "values": embeddings.vectors[i],
            "metadata": {"text": chunk, "file_id": file_id, "chunk_idx": i},
        }
        for i, chunk in enumerate(chunks)
    ]
    index.upsert(vectors=vectors)
    return {"upserted": len(chunks), "index": index_name}


# ── Qdrant ────────────────────────────────────────────────────────────────────

def upsert_qdrant(
    collection_name: str,
    url: str,
    api_key: str,
    chunks: List[str],
    embeddings: EmbeddingResult,  # Qdrant always requires vectors
    file_id: str,
) -> dict:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct, VectorParams, Distance
    except ImportError:
        raise RuntimeError(
            "qdrant-client not installed. Run: pip install qdrant-client"
        )

    client = QdrantClient(url=url or "http://localhost:6333", api_key=api_key or None)
    dims = embeddings.dims

    # Create collection if it doesn't exist yet
    try:
        client.get_collection(collection_name)
    except Exception:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
        )

    points = [
        PointStruct(
            id=abs(hash(f"{file_id}_chunk_{i}")) % (2 ** 63),
            vector=embeddings.vectors[i],
            payload={"text": chunk, "file_id": file_id, "chunk_idx": i},
        )
        for i, chunk in enumerate(chunks)
    ]
    client.upsert(collection_name=collection_name, points=points)
    return {"upserted": len(chunks), "collection": collection_name}
