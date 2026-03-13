"""
High-level RAG service: embed chunks then store in vector DB.
Both steps are optional — if the provider is "none" the function
returns success with vectorized=False so callers degrade gracefully.
"""
from __future__ import annotations
from typing import List, Tuple

from .config import rag_config
from .embeddings import (
    embed_openai,
    embed_sentence_transformers,
    embed_ollama,
    embed_google,
    EmbeddingResult,
)
from .vector_store import upsert_chroma, upsert_pinecone, upsert_qdrant


def get_rag_status() -> dict:
    """Return current RAG config for the status endpoint."""
    cfg = rag_config
    return {
        **cfg.as_dict(),
        "vector_db_api_key_set": bool(cfg.vector_db_api_key),
        "embedding_api_key_set": bool(cfg.embedding_api_key),
    }


def _get_embeddings(chunks: List[str]) -> Tuple[EmbeddingResult | None, str]:
    """Generate embeddings for all chunks. Returns (result, error_msg)."""
    cfg = rag_config
    provider = cfg.embedding_provider

    try:
        if provider == "openai":
            return embed_openai(chunks, cfg.embedding_model, cfg.embedding_api_key), ""
        if provider == "sentence-transformers":
            return embed_sentence_transformers(chunks, cfg.embedding_model), ""
        if provider == "ollama":
            return embed_ollama(chunks, cfg.embedding_model, cfg.embedding_base_url), ""
        if provider == "google":
            return embed_google(chunks, cfg.embedding_model, cfg.embedding_api_key), ""
        return None, f"Unknown embedding provider: {provider}"
    except Exception as exc:
        return None, f"Embedding error ({provider}): {exc}"


def _store_vectors(
    chunks: List[str],
    embeddings: EmbeddingResult | None,
    file_id: str,
) -> Tuple[dict, str]:
    """Store chunks in vector DB. Returns (metadata, error_msg)."""
    cfg = rag_config
    db = cfg.vector_db

    try:
        if db == "chroma":
            meta = upsert_chroma(
                cfg.vector_db_collection, cfg.vector_db_url,
                chunks, embeddings, file_id,
            )
        elif db == "pinecone":
            if embeddings is None:
                return {}, "Pinecone requires an embedding provider"
            meta = upsert_pinecone(
                cfg.vector_db_collection, cfg.vector_db_api_key,
                chunks, embeddings, file_id,
            )
        elif db == "qdrant":
            if embeddings is None:
                return {}, "Qdrant requires an embedding provider"
            meta = upsert_qdrant(
                cfg.vector_db_collection, cfg.vector_db_url,
                cfg.vector_db_api_key, chunks, embeddings, file_id,
            )
        else:
            return {}, f"Unknown vector DB: {db}"
        return meta, ""
    except Exception as exc:
        return {}, f"Vector store error ({db}): {exc}"


def process_rag(
    chunks: List[str],
    file_id: str,
) -> Tuple[bool, str, dict]:
    """
    Generate embeddings and store in vector DB.

    Returns:
        (success, error_message, extra_metadata)
    """
    cfg = rag_config

    # Short-circuit: no vector DB or no embeddings configured
    if cfg.vector_db == "none" or cfg.embedding_provider == "none":
        return True, "", {"vectorized": False}

    # Step 1 — embeddings
    embeddings, emb_err = _get_embeddings(chunks)
    if emb_err:
        return False, emb_err, {"vectorized": False}

    # Step 2 — vector store
    store_meta, store_err = _store_vectors(chunks, embeddings, file_id)
    if store_err:
        return False, store_err, {"vectorized": False}

    return True, "", {
        "vectorized": True,
        "embedding_provider": cfg.embedding_provider,
        "embedding_model": cfg.embedding_model,
        "embedding_dims": embeddings.dims if embeddings else 0,
        "vector_db": cfg.vector_db,
        "vector_db_collection": cfg.vector_db_collection,
        **store_meta,
    }
