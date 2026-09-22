"""
Embedding provider adapters.
Each function returns an EmbeddingResult or raises RuntimeError with
an install hint if the required package is missing.
"""
from __future__ import annotations
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Tuple


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
