"""
Embedding provider adapters.
Each function returns an EmbeddingResult or raises RuntimeError with
an install hint if the required package is missing.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List


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

def embed_sentence_transformers(texts: List[str], model: str) -> EmbeddingResult:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError(
            "sentence-transformers not installed. Run: pip install sentence-transformers"
        )
    st = SentenceTransformer(model)
    vectors = st.encode(texts, convert_to_numpy=True).tolist()
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
