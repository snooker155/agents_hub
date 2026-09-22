"""Ranking for the recall path.

Every memory layer used to answer with an unordered list of substring matches:
whatever the scan happened to hit, in the order the file stored it. This module
gives recall one ordering across the layers.

The formula, for a candidate from any layer:

    score = Σ over ranked lists of 1 / (RRF_K + rank)      # reciprocal rank fusion
          + EXACT_NAME_BONUS   when the query IS the block/slot/note name
          + LEXICAL_BONUS      when a query token appears as a substring
          + RECENCY_WEIGHT * decay(age)   for episodes only

The ranked lists are the BM25 ranking over the pool's own text and, when a
vector store answered, the similarity ranking of its hits. Reciprocal rank
fusion is used rather than a weighted sum of the raw numbers because a BM25
score and a cosine similarity are not on the same scale, and their scales move
with the corpus.

BM25 comes from ``rank_bm25`` when that package is installed, otherwise from
the small implementation below. Both produce the same ordering for the sizes a
memory pool reaches, so nothing here depends on the dependency being present.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

# Reciprocal rank fusion constant. 60 is the value the original RRF paper used
# and the one every implementation since has kept: large enough that the top of
# a list does not dominate, small enough that rank 1 still wins.
RRF_K = 60
EXACT_NAME_BONUS = 1.0
LEXICAL_BONUS = 0.01
RECENCY_WEIGHT = 0.01
RECENCY_HALF_LIFE_DAYS = 30.0

_WORD_RE = re.compile(r"[a-z0-9_]+")

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "by", "for", "to", "from", "with", "and", "or",
    "what", "who", "which", "where", "when", "why", "how", "does", "do", "did",
    "this", "that", "these", "those", "any", "some", "all", "i", "you", "we",
    "they", "it", "me", "us", "them", "my", "your", "our", "their", "its",
    "about", "tell", "show", "list", "give", "find", "have", "has", "had",
}


def terms(text: str) -> List[str]:
    """Lowercased content words, with a naive singular folded in.

    The singular form is added rather than substituted so that both "spells"
    and "spell" match a document holding either.
    """
    out: List[str] = []
    for raw in _WORD_RE.findall((text or "").lower()):
        if len(raw) < 2 or raw in _STOPWORDS:
            continue
        out.append(raw)
        if raw.endswith("ies") and len(raw) > 4:
            out.append(raw[:-3] + "y")
        elif raw.endswith("es") and len(raw) > 3:
            out.append(raw[:-2])
        elif raw.endswith("s") and len(raw) > 3:
            out.append(raw[:-1])
    return out


# ── BM25 ─────────────────────────────────────────────────────────────────────

class _BM25:
    """Okapi BM25 over an in-memory corpus of tokenised documents."""

    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75):
        self.corpus = [list(doc) for doc in corpus]
        self.k1 = k1
        self.b = b
        self.n = len(self.corpus)
        self.doc_len = [len(doc) for doc in self.corpus]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.freqs: List[Dict[str, int]] = []
        df: Dict[str, int] = {}
        for doc in self.corpus:
            counts: Dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self.freqs.append(counts)
            for token in counts:
                df[token] = df.get(token, 0) + 1
        # Standard BM25+ idf floor: a term in nearly every document scores a
        # little above zero instead of going negative and pushing hits down.
        self.idf = {
            token: max(
                0.05,
                math.log((self.n - freq + 0.5) / (freq + 0.5) + 1.0),
            )
            for token, freq in df.items()
        }

    def scores(self, query_terms: Sequence[str]) -> List[float]:
        out: List[float] = []
        for idx in range(self.n):
            counts = self.freqs[idx]
            length = self.doc_len[idx] or 1
            total = 0.0
            for token in query_terms:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                idf = self.idf.get(token, 0.0)
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avgdl or 1))
                total += idf * (tf * (self.k1 + 1)) / denom
            out.append(total)
        return out


def bm25_scores(query: str, documents: Sequence[str]) -> List[float]:
    """BM25 score per document, using ``rank_bm25`` when it is installed."""
    tokenised = [terms(doc) for doc in documents]
    q = terms(query)
    if not q or not tokenised:
        return [0.0] * len(documents)
    try:  # optional dependency; the fallback below is equivalent for our sizes
        from rank_bm25 import BM25Okapi  # type: ignore

        return [float(s) for s in BM25Okapi(tokenised).get_scores(q)]
    except Exception:
        return _BM25(tokenised).scores(q)


# ── Candidates ───────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """One rankable item from a memory layer."""

    key: str                      # unique within one ranking run
    layer: str                    # block | slot | note | episode | rag | graph
    text: str                     # what BM25 reads
    payload: Dict[str, Any] = field(default_factory=dict)
    name: str = ""                # block/slot name or note title, for exact match
    occurred_at: Optional[datetime] = None   # episodes only, drives the recency boost


def _recency_boost(occurred_at: Optional[datetime]) -> float:
    if occurred_at is None:
        return 0.0
    try:
        moment = occurred_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 86400.0)
    except Exception:
        return 0.0
    return RECENCY_WEIGHT * (0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def rank_candidates(
    query: str,
    candidates: Sequence[Candidate],
    *,
    vector_keys: Optional[Sequence[str]] = None,
    min_score: float = 0.0,
) -> List[tuple]:
    """Rank *candidates* for *query*, best first.

    ``vector_keys`` is the keys of the candidates a vector store returned, in
    its own similarity order; that ordering is fused with the BM25 one. Returns
    ``[(candidate, score), …]`` dropping anything at or below ``min_score``.
    """
    if not candidates:
        return []

    q_lower = (query or "").strip().lower()
    q_terms = terms(query)

    scores = bm25_scores(query, [c.text for c in candidates])
    by_key: Dict[str, float] = {}

    # BM25 ranking: only documents that actually matched a term take a rank.
    matched = sorted(
        [(c, s) for c, s in zip(candidates, scores) if s > 0.0],
        key=lambda pair: pair[1],
        reverse=True,
    )
    for rank, (cand, _s) in enumerate(matched, start=1):
        by_key[cand.key] = by_key.get(cand.key, 0.0) + _rrf(rank)

    # Vector ranking, when a store answered.
    for rank, key in enumerate(vector_keys or (), start=1):
        by_key[key] = by_key.get(key, 0.0) + _rrf(rank)

    ranked: List[tuple] = []
    for cand in candidates:
        score = by_key.get(cand.key, 0.0)
        name = (cand.name or "").strip().lower()
        if name and q_lower and name == q_lower:
            score += EXACT_NAME_BONUS
        haystack = f"{cand.name} {cand.text}".lower()
        if any(t in haystack for t in q_terms) or (q_lower and q_lower in haystack):
            score += LEXICAL_BONUS
        score += _recency_boost(cand.occurred_at)
        if score > min_score:
            ranked.append((cand, round(score, 6)))

    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


# ── Pool-wide collection ─────────────────────────────────────────────────────

def pool_candidates(
    mem,
    *,
    include_episodes: bool = True,
    episode_limit: int = 50,
    pool_id: Optional[str] = None,
) -> List[Candidate]:
    """Every rankable item in one pool: blocks, slots, notes and episodes.

    Journal notes are skipped — they are an append-only log the agent reads by
    date, and their bulk would otherwise swamp every other layer.
    """
    import json as _json

    from memory.tool import JOURNAL_PREFIX

    pid = str(pool_id or getattr(mem, "id", "") or "")
    out: List[Candidate] = []

    for block in getattr(mem, "blocks", []) or []:
        if not (block.value or "").strip():
            continue
        out.append(Candidate(
            key=f"{pid}:block:{block.name}",
            layer="block",
            text=f"{block.name} {block.description} {block.value}",
            name=block.name,
            payload={"source": "block", "block": block.name, "value": block.value},
        ))

    for slot, data in (getattr(mem, "structured_data", {}) or {}).items():
        try:
            data_str = _json.dumps(data, ensure_ascii=False, default=str)
        except Exception:
            data_str = str(data)
        out.append(Candidate(
            key=f"{pid}:slot:{slot}",
            layer="slot",
            text=f"{slot} {data_str}",
            name=slot,
            payload={"source": "structured", "slot": slot, "data": data},
        ))

    for note in getattr(mem, "notes", []) or []:
        title = note.get("title", "")
        if title.startswith(JOURNAL_PREFIX):
            continue
        content = note.get("content", "")
        out.append(Candidate(
            key=f"{pid}:note:{title}",
            layer="note",
            text=f"{title} {content}",
            name=title,
            payload={"source": "note", "title": title, "content": content},
        ))

    if include_episodes and pid:
        try:
            from memory.episodic import EpisodeStore

            episodes = EpisodeStore(pid).load()
            episodes.sort(key=lambda e: e.occurred_at, reverse=True)
            for ep in episodes[:episode_limit]:
                try:
                    details = _json.dumps(ep.details, ensure_ascii=False, default=str) if ep.details else ""
                except Exception:
                    details = ""
                out.append(Candidate(
                    key=f"{pid}:episode:{ep.id}",
                    layer="episode",
                    text=" ".join(filter(None, [ep.summary, ep.subject or "", " ".join(ep.tags), details])),
                    name="",
                    occurred_at=ep.occurred_at,
                    payload={
                        "source": "episode",
                        "id": str(ep.id),
                        "kind": ep.kind,
                        "summary": ep.summary,
                        "outcome": ep.outcome,
                        "occurred_at": ep.occurred_at.isoformat(),
                    },
                ))
        except Exception:
            pass

    return out
