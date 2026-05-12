"""
High-level RAG service: embed chunks then store in vector DB.
Both steps are optional — if the provider is "none" the function
returns success with vectorized=False so callers degrade gracefully.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, List, Optional, Tuple

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


def process_rag_with_progress(
    chunks: List[str],
    file_id: str,
    on_event: Callable[[dict], None],
) -> Tuple[bool, str, dict]:
    """
    Like process_rag but calls on_event for each progress step so callers
    can stream status to clients.  on_event receives dicts with a "type" key.
    """
    cfg = rag_config

    on_event({"type": "chunked", "chunks": len(chunks)})

    if cfg.vector_db == "none" or cfg.embedding_provider == "none":
        result: dict = {"vectorized": False}
        on_event({"type": "done", **result})
        return True, "", result

    on_event({
        "type": "embedding_start",
        "provider": cfg.embedding_provider,
        "model": cfg.embedding_model,
        "total": len(chunks),
    })

    embeddings: EmbeddingResult | None = None
    emb_err = ""
    provider = cfg.embedding_provider

    try:
        if provider == "ollama":
            # Embed one chunk at a time so we can report per-chunk progress.
            vectors: list = []
            for i, chunk in enumerate(chunks):
                r = embed_ollama([chunk], cfg.embedding_model, cfg.embedding_base_url)
                vectors.extend(r.vectors)
                on_event({"type": "embedding_progress", "done": i + 1, "total": len(chunks)})
            embeddings = EmbeddingResult(vectors=vectors, model=cfg.embedding_model, provider="ollama")
        elif provider == "openai":
            embeddings = embed_openai(chunks, cfg.embedding_model, cfg.embedding_api_key)
            on_event({"type": "embedding_progress", "done": len(chunks), "total": len(chunks)})
        elif provider == "sentence-transformers":
            embeddings = embed_sentence_transformers(chunks, cfg.embedding_model)
            on_event({"type": "embedding_progress", "done": len(chunks), "total": len(chunks)})
        elif provider == "google":
            embeddings = embed_google(chunks, cfg.embedding_model, cfg.embedding_api_key)
            on_event({"type": "embedding_progress", "done": len(chunks), "total": len(chunks)})
        else:
            emb_err = f"Unknown embedding provider: {provider}"
    except Exception as exc:
        emb_err = f"Embedding error ({provider}): {exc}"

    if emb_err:
        on_event({"type": "error", "message": emb_err})
        return False, emb_err, {"vectorized": False}

    on_event({"type": "storing", "db": cfg.vector_db, "collection": cfg.vector_db_collection})

    store_meta, store_err = _store_vectors(chunks, embeddings, file_id)
    if store_err:
        on_event({"type": "error", "message": store_err})
        return False, store_err, {"vectorized": False}

    result = {
        "vectorized": True,
        "embedding_provider": cfg.embedding_provider,
        "embedding_model": cfg.embedding_model,
        "embedding_dims": embeddings.dims if embeddings else 0,
        "vector_db": cfg.vector_db,
        "vector_db_collection": cfg.vector_db_collection,
        **store_meta,
    }
    on_event({"type": "done", **result})
    return True, "", result


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


# ---------------------------------------------------------------------------
# File ingestion — read a file from disk, chunk it, index into a pool
# ---------------------------------------------------------------------------

_SUPPORTED_EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".html", ".xml"}
_CHUNK_SIZE = 800   # characters
_CHUNK_OVERLAP = 100


def _read_file_text(path: Path) -> Optional[str]:
    """Read plain text from a file. Returns None for unsupported/binary files."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            try:
                import PyPDF2  # type: ignore
                reader = PyPDF2.PdfReader(str(path))
                return "\n\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                return None
    if suffix in _SUPPORTED_EXTENSIONS or suffix == "":
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
    return None


def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping character-level chunks."""
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]


def ingest_file(file_path: Path, pool_id: str) -> Tuple[bool, str, int]:
    """Read *file_path*, chunk it, and index into the vector store under *pool_id*.

    file_id in the vector store is ``{pool_id}::{filename}`` so queries can
    filter by pool.

    Returns:
        (success, error_message, chunk_count)
    """
    text = _read_file_text(file_path)
    if text is None:
        return False, f"Unsupported or unreadable file: {file_path.name}", 0

    text = text.strip()
    if not text:
        return False, "File is empty", 0

    chunks = _chunk_text(text)
    file_id = f"{pool_id}::{file_path.name}"
    ok, err, _ = process_rag(chunks, file_id)
    if not ok:
        return False, err, 0
    return True, "", len(chunks)
