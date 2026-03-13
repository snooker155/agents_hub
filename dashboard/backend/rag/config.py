"""
RAG configuration — reads env vars at call time so changes via the settings
API take effect without restarting the backend module.
"""
from __future__ import annotations
import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip().strip('"\'')


class RagConfig:
    """Live view of RAG-related environment variables."""

    # ── Vector store ──────────────────────────────────────────────────────────
    @property
    def vector_db(self) -> str:
        """Provider: none | chroma | pinecone | qdrant"""
        return _env("RAG_VECTOR_DB", "none").lower()

    @property
    def vector_db_url(self) -> str:
        return _env("RAG_VECTOR_DB_URL", "")

    @property
    def vector_db_api_key(self) -> str:
        return _env("RAG_VECTOR_DB_API_KEY", "")

    @property
    def vector_db_collection(self) -> str:
        return _env("RAG_VECTOR_DB_COLLECTION", "agents_hub_rag")

    # ── Embedding model ───────────────────────────────────────────────────────
    @property
    def embedding_provider(self) -> str:
        """Provider: none | openai | sentence-transformers | ollama | google"""
        return _env("RAG_EMBEDDING_PROVIDER", "none").lower()

    @property
    def embedding_model(self) -> str:
        return _env("RAG_EMBEDDING_MODEL", "text-embedding-3-small")

    @property
    def embedding_api_key(self) -> str:
        key = _env("RAG_EMBEDDING_API_KEY", "")
        if not key and self.embedding_provider == "openai":
            key = _env("OPENAI_API_KEY", "")
        if not key and self.embedding_provider == "google":
            key = _env("GOOGLE_API_KEY", "")
        return key

    @property
    def embedding_base_url(self) -> str:
        return _env("RAG_EMBEDDING_BASE_URL", "http://localhost:11434")

    # ── Convenience ───────────────────────────────────────────────────────────
    @property
    def is_configured(self) -> bool:
        return self.vector_db != "none" and self.embedding_provider != "none"

    def as_dict(self) -> dict:
        return {
            "vector_db": self.vector_db,
            "vector_db_url": self.vector_db_url,
            "vector_db_collection": self.vector_db_collection,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_base_url": self.embedding_base_url,
            "is_configured": self.is_configured,
        }


rag_config = RagConfig()
