"""
Keyword search over a pool's indexed chunks — pure-Python BM25, no
dependency. Reads from ``rag_chunks`` (``chunk_store.py``) exclusively, so it
answers regardless of whether a vector store or embedding provider is
configured: this is what makes RAG work out of the box with no torch.

Per-pool term statistics (document frequencies, average length) are cached in
process, since rebuilding the index on every query would mean re-tokenising
every chunk of the pool for every search. ``invalidate_pool`` drops the cache
for one pool; call it after any ingest, reindex or delete that touched
``rag_chunks``.
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Applied only when a caller asks for it (drop_stopwords=True); BM25's own
# idf term already pushes a word that appears in nearly every chunk close to
# zero, so leaving stopwords in is usually harmless and this stays optional.
STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "by", "for", "to", "from", "with", "and", "or",
    "this", "that", "these", "those", "it", "its", "as", "into", "than",
})


def tokenize(text: str, *, drop_stopwords: bool = False) -> List[str]:
    """Lowercase Unicode word tokens. Kept dumb on purpose: BM25's own
    idf/tf weighting does most of the work, and a smarter tokenizer would need
    a language to be smart in."""
    tokens = [t.lower() for t in _WORD_RE.findall(text or "")]
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return tokens


@dataclass
class BM25Index:
    """A BM25 index over a fixed corpus, addressed by an opaque ``doc_id``
    per document rather than by position, so a caller can map a hit straight
    back to the chunk row it came from."""

    doc_ids: List[str]
    doc_len: List[int]
    avgdl: float
    freqs: List[Dict[str, int]]
    idf: Dict[str, float]
    k1: float = 1.5
    b: float = 0.75

    def scores(self, query_terms: Sequence[str]) -> List[float]:
        out: List[float] = []
        for i in range(len(self.doc_ids)):
            counts = self.freqs[i]
            length = self.doc_len[i] or 1
            total = 0.0
            for term in query_terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avgdl or 1))
                total += idf * (tf * (self.k1 + 1)) / denom
            out.append(total)
        return out

    def search(self, query: str, top_k: int = 10, *, drop_stopwords: bool = False) -> List[Tuple[str, float]]:
        q_terms = tokenize(query, drop_stopwords=drop_stopwords)
        if not q_terms or not self.doc_ids:
            return []
        scores = self.scores(q_terms)
        ranked = sorted(
            ((doc_id, s) for doc_id, s in zip(self.doc_ids, scores) if s > 0.0),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked[:top_k]


def build_index(doc_ids: Sequence[str], texts: Sequence[str], *, drop_stopwords: bool = False) -> BM25Index:
    tokenised = [tokenize(t, drop_stopwords=drop_stopwords) for t in texts]
    n = len(tokenised)
    doc_len = [len(d) for d in tokenised]
    avgdl = (sum(doc_len) / n) if n else 0.0

    freqs: List[Dict[str, int]] = []
    df: Dict[str, int] = {}
    for doc in tokenised:
        counts: Dict[str, int] = {}
        for tok in doc:
            counts[tok] = counts.get(tok, 0) + 1
        freqs.append(counts)
        for tok in counts:
            df[tok] = df.get(tok, 0) + 1

    # BM25+-style idf floor: a term in nearly every document scores a little
    # above zero instead of going negative and pushing its chunk down.
    idf = {
        tok: max(0.05, math.log((n - freq + 0.5) / (freq + 0.5) + 1.0))
        for tok, freq in df.items()
    }
    return BM25Index(doc_ids=list(doc_ids), doc_len=doc_len, avgdl=avgdl, freqs=freqs, idf=idf)


# ---------------------------------------------------------------------------
# Per-pool cache
# ---------------------------------------------------------------------------

_CACHE: Dict[str, BM25Index] = {}
_CACHE_CHUNKS: Dict[str, List[dict]] = {}
_LOCK = threading.Lock()


def invalidate_pool(pool_id: str) -> None:
    """Drop the cached index for one pool. Call after any write to its
    ``rag_chunks`` rows (ingest, reindex, delete)."""
    with _LOCK:
        _CACHE.pop(str(pool_id), None)
        _CACHE_CHUNKS.pop(str(pool_id), None)


def clear_cache() -> None:
    """Drop every pool's cached index. Used by tests."""
    with _LOCK:
        _CACHE.clear()
        _CACHE_CHUNKS.clear()


def _chunk_doc_id(chunk: dict) -> str:
    return f"{chunk['file_id']}#{chunk['chunk_index']}"


def pool_index(pool_id: str) -> Tuple[BM25Index, List[dict]]:
    """The cached BM25 index for *pool_id* and the chunk rows it was built
    from (same order as ``doc_ids``), building and caching it on first use."""
    pool_id = str(pool_id)
    with _LOCK:
        cached = _CACHE.get(pool_id)
        if cached is not None:
            return cached, _CACHE_CHUNKS[pool_id]

    from .chunk_store import pool_chunks

    chunks = pool_chunks(pool_id)
    doc_ids = [_chunk_doc_id(c) for c in chunks]
    texts = [c["text"] for c in chunks]
    index = build_index(doc_ids, texts)

    with _LOCK:
        _CACHE[pool_id] = index
        _CACHE_CHUNKS[pool_id] = chunks
    return index, chunks


def search_pool(pool_id: str, query: str, top_k: int = 10) -> List[dict]:
    """BM25 search over one pool's chunks. Each result is the chunk row plus
    a ``bm25_score``, best first."""
    index, chunks = pool_index(pool_id)
    if not chunks:
        return []
    by_id = {_chunk_doc_id(c): c for c in chunks}
    hits = index.search(query, top_k=top_k)
    out: List[dict] = []
    for doc_id, score in hits:
        chunk = by_id.get(doc_id)
        if chunk is None:
            continue
        out.append({**chunk, "bm25_score": score})
    return out
