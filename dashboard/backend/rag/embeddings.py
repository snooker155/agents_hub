"""
Embedding provider adapters, and the Embedder that wraps them.

Each ``embed_*`` function returns an EmbeddingResult or raises RuntimeError
with an install hint if the required package is missing. :class:`Embedder` is
the process-wide object built on top of them: it resolves a provider name to
the right function, is shared by ingestion (``service.py``) and query
(``memory/rag_query.py``) so the model behind it is loaded once per process
rather than once per caller, and keeps a small cache of recent query vectors.
"""
from __future__ import annotations
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class EmbeddingResult:
    vectors: List[List[float]]
    model: str
    provider: str
    dims: int = 0

    def __post_init__(self):
        if self.vectors and not self.dims:
            self.dims = len(self.vectors[0])


# ── OpenAI ────────────────────────────────────────────────────────────────────

def embed_openai(texts: List[str], model: str, api_key: str) -> EmbeddingResult:
    try:
        import openai
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")
    if not api_key:
        raise RuntimeError("OpenAI API key is not set (OPENAI_API_KEY / RAG_EMBEDDING_API_KEY)")
    client = openai.OpenAI(api_key=api_key)
    resp = client.embeddings.create(input=texts, model=model)
    vectors = [d.embedding for d in resp.data]
    return EmbeddingResult(vectors=vectors, model=model, provider="openai")


# ── Sentence-Transformers ─────────────────────────────────────────────────────

# Loading a SentenceTransformer reads the weights from disk and builds the torch
# graph: seconds, and hundreds of megabytes. It was paid again on every call,
# including once per query. One instance per model name per process instead.
_ST_MODELS: Dict[str, object] = {}
_ST_LOCK = threading.Lock()

# Repeated texts are common: the same query asked twice, the same chunk
# re-indexed. A small LRU keeps the last few hundred vectors rather than
# re-running the model over text it has already seen.
_EMBED_CACHE_SIZE = 512
_EMBED_CACHE: "OrderedDict[Tuple[str, str], List[float]]" = OrderedDict()
_EMBED_CACHE_LOCK = threading.Lock()


def get_sentence_transformer(model: str):
    """Return the process-wide SentenceTransformer for *model*, loading once."""
    st = _ST_MODELS.get(model)
    if st is not None:
        return st
    with _ST_LOCK:
        st = _ST_MODELS.get(model)
        if st is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers not installed. Run: pip install sentence-transformers"
                )
            st = SentenceTransformer(model)
            _ST_MODELS[model] = st
    return st


def _cache_get(model: str, text: str):
    with _EMBED_CACHE_LOCK:
        key = (model, text)
        if key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(key)
            return list(_EMBED_CACHE[key])
    return None


def _cache_put(model: str, text: str, vector: List[float]) -> None:
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE[(model, text)] = list(vector)
        _EMBED_CACHE.move_to_end((model, text))
        while len(_EMBED_CACHE) > _EMBED_CACHE_SIZE:
            _EMBED_CACHE.popitem(last=False)


def clear_embedding_cache() -> None:
    """Drop the cached vectors (not the loaded models). Used by tests."""
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE.clear()


def embed_sentence_transformers(texts: List[str], model: str) -> EmbeddingResult:
    vectors: List[List[float]] = [None] * len(texts)  # type: ignore[list-item]
    todo: List[int] = []
    for i, text in enumerate(texts):
        hit = _cache_get(model, text)
        if hit is None:
            todo.append(i)
        else:
            vectors[i] = hit

    if todo:
        st = get_sentence_transformer(model)
        fresh = st.encode([texts[i] for i in todo], convert_to_numpy=True).tolist()
        for i, vector in zip(todo, fresh):
            vectors[i] = vector
            _cache_put(model, texts[i], vector)

    return EmbeddingResult(vectors=vectors, model=model, provider="sentence-transformers")


# ── Ollama ────────────────────────────────────────────────────────────────────

def embed_ollama(texts: List[str], model: str, base_url: str) -> EmbeddingResult:
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests package not installed. Run: pip install requests")
    url = base_url.rstrip("/") + "/api/embeddings"
    vectors = []
    for text in texts:
        resp = requests.post(url, json={"model": model, "prompt": text}, timeout=30)
        resp.raise_for_status()
        vectors.append(resp.json()["embedding"])
    return EmbeddingResult(vectors=vectors, model=model, provider="ollama")


# ── Google (Gemini) ───────────────────────────────────────────────────────────

def embed_google(texts: List[str], model: str, api_key: str) -> EmbeddingResult:
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError(
            "google-generativeai not installed. Run: pip install google-generativeai"
        )
    if not api_key:
        raise RuntimeError("Google API key not set (GOOGLE_API_KEY / RAG_EMBEDDING_API_KEY)")
    genai.configure(api_key=api_key)
    vectors = []
    for text in texts:
        result = genai.embed_content(
            model=model, content=text, task_type="retrieval_document"
        )
        vectors.append(result["embedding"])
    return EmbeddingResult(vectors=vectors, model=model, provider="google")


# ── Embedder: the shared, long-lived object ───────────────────────────────────

# A single query text is common across calls (the same question asked twice,
# a query re-embedded after a cache miss upstream). Keyed on (provider, model,
# text) so two providers sharing a model name, or a config change, never read
# a stale vector back.
_QUERY_CACHE_SIZE = 256
_QUERY_CACHE: "OrderedDict[Tuple[str, str, str], List[float]]" = OrderedDict()
_QUERY_CACHE_LOCK = threading.Lock()


def _query_cache_get(provider: str, model: str, text: str):
    with _QUERY_CACHE_LOCK:
        key = (provider, model, text)
        if key in _QUERY_CACHE:
            _QUERY_CACHE.move_to_end(key)
            return list(_QUERY_CACHE[key])
    return None


def _query_cache_put(provider: str, model: str, text: str, vector: List[float]) -> None:
    with _QUERY_CACHE_LOCK:
        key = (provider, model, text)
        _QUERY_CACHE[key] = list(vector)
        _QUERY_CACHE.move_to_end(key)
        while len(_QUERY_CACHE) > _QUERY_CACHE_SIZE:
            _QUERY_CACHE.popitem(last=False)


def clear_query_cache() -> None:
    """Drop the cached query vectors. Used by tests."""
    with _QUERY_CACHE_LOCK:
        _QUERY_CACHE.clear()


class Embedder:
    """The process-wide embedder: one instance per (provider, model, key,
    base_url) combination, so the sentence-transformers weights behind it
    (``get_sentence_transformer``) are loaded once and reused by every caller
    in the process — ingestion and query alike — instead of each caller
    loading its own copy.

    ``for_config`` is the usual way to get one: it hands back the process-wide
    instance for that exact config, building it lazily on first use and
    replacing it if the config has since changed (a different model, a
    rotated key). Holding a reference from ``Embedder(...)`` directly is only
    for tests that want an isolated instance.
    """

    _shared: "Optional[Embedder]" = None
    _shared_lock = threading.Lock()

    def __init__(self, provider: str, model: str, *, api_key: str = "", base_url: str = ""):
        self.provider = (provider or "none").lower()
        self.model = model or ""
        self.api_key = api_key or ""
        self.base_url = base_url or ""

    def _config_key(self) -> Tuple[str, str, str, str]:
        return (self.provider, self.model, self.api_key, self.base_url)

    @classmethod
    def for_config(cls, provider: str, model: str, *, api_key: str = "", base_url: str = "") -> "Embedder":
        wanted = (
            (provider or "none").lower(), model or "", api_key or "", base_url or "",
        )
        with cls._shared_lock:
            inst = cls._shared
            if inst is None or inst._config_key() != wanted:
                inst = cls(provider, model, api_key=api_key, base_url=base_url)
                cls._shared = inst
            return inst

    def is_configured(self) -> bool:
        return self.provider not in ("", "none") and bool(self.model)

    def embed(self, texts: List[str]) -> EmbeddingResult:
        """Embed a batch of texts (ingestion's path). Raises RuntimeError for
        an unknown or unconfigured provider, or whatever the provider adapter
        raises (missing package, missing key, request failure)."""
        if not self.is_configured():
            raise RuntimeError("No embedding provider configured")
        if self.provider == "openai":
            return embed_openai(texts, self.model, self.api_key)
        if self.provider == "sentence-transformers":
            return embed_sentence_transformers(texts, self.model)
        if self.provider == "ollama":
            return embed_ollama(texts, self.model, self.base_url or "http://localhost:11434")
        if self.provider == "google":
            return embed_google(texts, self.model, self.api_key)
        raise RuntimeError(f"Unknown embedding provider: {self.provider}")

    def embed_one(self, text: str) -> Optional[List[float]]:
        """Embed one text (query's path), through the short-lived query
        cache. Returns None rather than raising, so a caller can fall back to
        BM25-only search on any failure — a missing package, a network error,
        an unconfigured provider — without special-casing each one."""
        if not self.is_configured():
            return None
        cached = _query_cache_get(self.provider, self.model, text)
        if cached is not None:
            return cached
        try:
            result = self.embed([text])
        except Exception:
            return None
        vector = result.vectors[0] if result.vectors else None
        if vector is not None:
            _query_cache_put(self.provider, self.model, text, vector)
        return vector
